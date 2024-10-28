import dotenv;dotenv.load_dotenv()

from types import SimpleNamespace
from PIL import Image
import torch
import numpy as np
from io import BytesIO, StringIO
from collections import deque
import traceback
import threading
from backed_bot_utils import telegram_reply_to, get_username, handle_exception, get_dbm, all_logging_disabled
import os, gc, inspect
from telebot import types, TeleBot
from tqdm import tqdm

def get_full_image_id(user_id, image_id):
    return f"{user_id}:{image_id}"

NODES_TO_CACHE = os.environ.get("NODES_TO_CACHE", '')
NODE_OUTPUT_CACHES = {}
NODES_TO_TRACK_PBAR = os.environ.get("NODES_TO_TRACK_PBAR", '')

def create_hooks(self, message: types.Message, parsed_data: dict, image_output_callback):
    def handle_string_input(required, string, argument_name):
        if required and argument_name not in parsed_data:
            if argument_name == "prompt": raise RuntimeError("A prompt is required")
            else: raise RuntimeError(f"Argument --{argument_name} is required")
        return (parsed_data.get(argument_name, string),)
    
    def handle_string_output(string):
        if len(string.strip()) == 0:
            raise RuntimeError(f"String passed to StringOutput node must not be empty")
        telegram_reply_to(self.bot, message, string)

    def handle_image_input(**kwargs):
        if message.content_type != "photo":
            raise RuntimeError(f"This command requires an image")
        file_info = self.bot.get_file(max(message.photo, key=lambda p:p.width).file_id)
        img = Image.open(BytesIO(self.bot.download_file(file_info.file_path)))
        return (torch.from_numpy(np.array(img)[:, :, :3]/255.).float().unsqueeze(0),)
    
    def handle_image_input_from_id(argument_name):
        _image_id = parsed_data.get(argument_name, '') or ''
        if len(_image_id) == 0:
            raise RuntimeError(f"Argument --{argument_name} is required, with the value being image id")
        
        if _image_id.startswith("TG-"):
            file_info = self.bot.get_file(_image_id[len("TG-"):])
        else:
            with get_dbm("image_ids") as image_ids:
                image_id = get_full_image_id(message.from_user.id, _image_id)
                if image_id not in image_ids:
                    raise RuntimeError(f"Image_id {_image_id} isn't set for user {get_username(message.from_user)} ({message.from_user.id}). Run `/set_image_id {_image_id}` with a photo")
                file_id = image_ids[image_id]
                file_info = self.bot.get_file(file_id)

        img = Image.open(BytesIO(self.bot.download_file(file_info.file_path)))
        return (torch.from_numpy(np.array(img)[:, :, :3]/255.).unsqueeze(0),)
    
    def handle_image_output(image):
        image_pil = Image.fromarray(image[0, :, :, :3].cpu().numpy().__mul__(255.).astype(np.uint8))
        image_bytes = BytesIO()
        image_pil.save(image_bytes, format="PNG")
        image_bytes.seek(0)
        if image_output_callback is not None:
            image_output_callback(image_bytes)
        else:
            telegram_reply_to(self.bot, message, image_bytes)
    
    def handle_integer_input(required, integer, integer_min, integer_max, argument_name):
        if argument_name not in parsed_data:
            raise RuntimeError(f"Missing argument {argument_name}")
        if required and argument_name not in parsed_data:
            raise RuntimeError(f"Argument --{argument_name} is required")
        warning_msg = ''
        if integer < integer_min:
            warning_msg += f"The minium of --{argument_name} is {integer_min}. Changing to that value\n"
        if integer > integer_min:
            warning_msg += f"The maximum of --{argument_name} is {integer_min}. Changing to that value\n"
        if len(warning_msg): telegram_reply_to(self.bot, message, warning_msg)
        integer = int(parsed_data.get(argument_name, integer))
        integer = max(integer_max, min(integer, integer_min))
        return (integer,)
    
    def handle_nodes_to_cache():
        hooks = {}
        not_installed_nodes = []
        for node_to_cache in map(lambda str: str.strip(), NODES_TO_CACHE.split(',')):
            if node_to_cache not in self.NODE_CLASS_MAPPINGS:
                not_installed_nodes.append(node_to_cache)
                continue
            node = self.NODE_CLASS_MAPPINGS[node_to_cache]()
            def cache_proxy(*arg, **kwargs):
                if node_to_cache not in NODE_OUTPUT_CACHES:
                    NODE_OUTPUT_CACHES[node_to_cache] = getattr(node, node.FUNCTION)(*arg, **kwargs)
                return NODE_OUTPUT_CACHES[node_to_cache]
            hooks[node_to_cache] = SimpleNamespace(**{node.FUNCTION: cache_proxy})
        if len(not_installed_nodes):
            raise NotImplementedError(f"The following nodes are not installed: {', '.join(not_installed_nodes)}")
        return hooks
                
    return {
        "AppIO_StringInput": SimpleNamespace(execute=handle_string_input),
        "AppIO_StringOutput": SimpleNamespace(execute=handle_string_output),
        "AppIO_ImageInput": SimpleNamespace(execute=handle_image_input),
        "AppIO_ImageOutput": SimpleNamespace(execute=handle_image_output),
        "AppIO_IntegerInput": SimpleNamespace(execute=handle_integer_input),
        "AppIO_ImageInputFromID": SimpleNamespace(execute=handle_image_input_from_id),
        **handle_nodes_to_cache()
    }

class NodeProgressBar:
    def __init__(self, bot: TeleBot, message: types.Message, node_class: str, total: int):
        self.bot = bot
        self.message = message
        self.orig_text = message.text
        self.node_class = node_class
        self.stream = StringIO()
        self.pbar = tqdm(desc='\n'+node_class, total=total, file=self.stream)
    
    def update(self, current):
        self.pbar.n = current
        self.pbar.refresh()
        self.bot.edit_message_text(
            self.orig_text + f"\n\n```{self.stream.getvalue()}\n```",
            self.message.chat.id,
            self.message.id,
            parse_mode="Markdown"
        )

class ComfyWorker:
    def __init__(self, bot: TeleBot):
        self.data = deque()
        self.bot = bot
        self.node_pbar = None
        self.NODE_CLASS_MAPPINGS = {}
        threading.Thread(target=self.loop_thread, daemon=True).start()
    
    def execute(self, command_name, message, parsed_data, pbar_message=None, image_output_callback=None):
        self.data.append((command_name, message, parsed_data, pbar_message, image_output_callback))
    
    def message_pbar_hook(self, message, current, total, preview):
        stack = inspect.stack()
        node_class = None
        for frame_info in reversed(stack):
            self_in_frame = frame_info.frame.f_locals.get('self', None)
            if self_in_frame is not None and hasattr(self_in_frame, "FUNCTION"):
                node_ids = [k for k, v in self.NODE_CLASS_MAPPINGS.items() if v.__name__ == self_in_frame.__class__.__name__]
                if len(node_ids) and any(map(lambda node_id: node_id in NODES_TO_TRACK_PBAR, node_ids)):
                    node_class = self_in_frame.__class__.__name__
                    break
        
        if node_class is None:
            self.node_pbar = None
            return
        if self.node_pbar is None or node_class != self.node_pbar.node_class or current == 1:
            self.node_pbar = NodeProgressBar(self.bot, message, node_class, total)
        self.node_pbar.update(current)

    def loop_thread(self):
        with all_logging_disabled():
            import preprocessed
            import comfy.model_management as mm
            from nodes import NODE_CLASS_MAPPINGS
            from comfy.utils import set_progress_bar_global_hook
            self.NODE_CLASS_MAPPINGS = NODE_CLASS_MAPPINGS
            
        print("Telegram bot running, listening for all commands")
        while True:
            if not self.data: continue
            command_name, message, parsed_data, pbar_message, image_output_callback = self.data.popleft()
            parsed_data["prompt"] = parsed_data["prompt"].replace("''", '')
            hooks = create_hooks(self, message, parsed_data, image_output_callback)
            if pbar_message is not None:
                set_progress_bar_global_hook(lambda *args: self.message_pbar_hook(pbar_message, *args))
            try:
                getattr(preprocessed, command_name)(hooks)
                mm.cleanup_models()
                gc.collect()
                mm.soft_empty_cache()
            except Exception as e:
                handle_exception(self.bot, message, e, traceback.format_exc())
            finally:
                set_progress_bar_global_hook(None)