import dotenv;dotenv.load_dotenv()

from types import SimpleNamespace
from PIL import Image
import torch
import numpy as np
from io import BytesIO, StringIO
from collections import deque
import threading
from backed_bot_utils import telegram_reply_to, get_username, handle_exception, get_dbm, all_logging_disabled, mention
import os, gc, inspect
from telebot import types, TeleBot
from tqdm import tqdm
import schedule
from pathlib import Path
import cv2
import tempfile

NODES_TO_CACHE = os.environ.get("NODES_TO_CACHE", '')
NODE_OUTPUT_CACHES = {}
NODES_TO_TRACK_PBAR = os.environ.get("NODES_TO_TRACK_PBAR", '')
TELEBOT_DEBUG = int(os.environ.get("TELEBOT_DEBUG", "0"))
IMAGE_FORMAT = os.environ.get("IMAGE_FORMAT", "png").upper()

def get_full_image_id(user_id, image_id):
    return f"{user_id}:{image_id}"

def read_video(byte_stream: BytesIO, suffix: str):
   # Create a temporary file
    temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)  
    temp_path = temp_file.name  

    try:
        # Write BytesIO content to the temp file and close it
        temp_file.write(byte_stream.getvalue())
        temp_file.close()  # Necessary for Windows!

        # Open video using OpenCV
        cap = cv2.VideoCapture(temp_path)

        frames = []
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)

        cap.release()

    finally:
        os.remove(temp_path)  # Clean up temp file. F.U. Windows
    return torch.from_numpy(np.stack(frames, axis=0)) / 255.

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
        if message.content_type not in ["photo", "video", "animation"]:
            raise RuntimeError(f"This command requires an image or video")
        if message.content_type == "photo":
            file_id = max(message.photo, key=lambda p:p.width).file_id
        else:
            file_id = getattr(message, message.content_type).file_id
        file_info = self.bot.get_file(file_id)
        byte_stream = BytesIO(self.bot.download_file(file_info.file_path))
        if (suffix:=Path(file_info.file_path).suffix.lower()) in [".mp4", ".avi", ".mov", ".mkv"]:
            return (read_video(byte_stream, suffix),)
        else:
            img = Image.open(byte_stream)
            return (torch.from_numpy(np.array(img)[:, :, :3]/255.).unsqueeze(0),)
    
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

        byte_stream = BytesIO(self.bot.download_file(file_info.file_path))
        if (suffix:=Path(file_info.file_path).suffix.lower()) in [".mp4", ".avi", ".mov", ".mkv"]:
            return (read_video(byte_stream, suffix),)
        else:
            img = Image.open(byte_stream)
            return (torch.from_numpy(np.array(img)[:, :, :3]/255.).unsqueeze(0),)
    
    def handle_image_output(image):
        image = image[..., :3].cpu().numpy().__mul__(255.).astype(np.uint8)
        image_pils = [Image.fromarray(img.squeeze(0)) for img in np.split(image, image.shape[0], axis=0)]
        if image_output_callback is not None:
            image_output_callback(image_pils)
        else:
            image_bytes = BytesIO()
            image_pils[0].save(image_bytes, format=IMAGE_FORMAT if len(image_pils) == 1 else "GIF", save_all=True, append_images=image_pils[1:])
            image_bytes.seek(0)
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
        class NodeProxy:
            def __init__(self, node, class_name):
                self.node = node
                self.class_name = class_name
            
            def __call__(self, **kwargs):
                key = f"{self.class_name}({kwargs})"
                if key not in NODE_OUTPUT_CACHES:
                    print(f"Caching node {key}...")
                    NODE_OUTPUT_CACHES[key] = getattr(self.node, self.node.FUNCTION)(**kwargs)
                return NODE_OUTPUT_CACHES[key]

            @property
            def hooker(self):
                return SimpleNamespace(**{self.node.FUNCTION: self})
        
        hooks = {}
        not_installed_nodes = []
        for node_to_cache in map(lambda str: str.strip(), NODES_TO_CACHE.split(',')):
            if node_to_cache not in self.NODE_CLASS_MAPPINGS:
                not_installed_nodes.append(node_to_cache)
                continue
            node = self.NODE_CLASS_MAPPINGS[node_to_cache]()
            hooks[node_to_cache] = NodeProxy(node, node_to_cache).hooker
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

class Request:
    def __init__(self, bot: TeleBot, index: int, queue_len: int, orig_message: types.Message, message: types.Message, data):
        self.bot = bot
        self.index = index
        self.queue_len = queue_len
        self.message = message
        self.data = data
        self.orig_message = orig_message
        self.update_queue_job = schedule.every(2.5).seconds.do(self.update_queue)
        self.update_queue_job.run()
    
    def update_queue(self):
        if self.message is not None:
            user = self.orig_message.from_user
            text = f"{mention(user)} Queuing: `{self.queue_len - self.index}` people ahead ({self.index+1}/{self.queue_len})..."
            rendered_text = f"@{get_username(user)} Queuing: {self.queue_len - self.index} people ahead ({self.index+1}/{self.queue_len})..."
            if self.message.text != rendered_text:
                self.message = self.bot.edit_message_text(
                    text,
                    self.message.chat.id, self.message.id, parse_mode="Markdown"
                )
    
    def pop(self):
        schedule.cancel_job(self.update_queue_job)
        if self.message is not None:
            self.message = self.bot.edit_message_text(
                f"Executing...",
                self.message.chat.id, self.message.id, parse_mode="Markdown"
            )
        return self.data

class ComfyWorker:
    def __init__(self, bot: TeleBot):
        self.request_queue: deque[Request] = deque()
        self.bot = bot
        self.node_pbar = None
        self.NODE_CLASS_MAPPINGS = {}
        threading.Thread(target=self.loop_thread, daemon=True).start()
        self.execute_lock = threading.Lock()
        self.executing_user_id = None

    def execute(self, command_name, message: types.Message, parsed_data, pbar_message: types.Message=None, image_output_callback=None):
        with self.execute_lock:
            user_id = str(message.from_user.id)
            user_ids_in_queue = set([str(req.orig_message.from_user.id) for req in self.request_queue])
            if user_id in user_ids_in_queue or user_id == self.executing_user_id:
                if pbar_message is not None:
                    return self.bot.edit_message_text(
                        f"{mention(message.from_user)} Multiple simultaneous requests are not allowed", 
                        pbar_message.chat.id, pbar_message.id, parse_mode="Markdown"
                    )
            
            self.request_queue.append(Request(
                self.bot, len(self.request_queue), len(self.request_queue)+1, message, pbar_message, 
                (pbar_message, command_name, message, parsed_data, image_output_callback)
            ))
    
    def get_request(self):
        curr_req = self.request_queue.popleft()
        self.executing_user_id = str(curr_req.orig_message.from_user.id)
        for idx, req in enumerate(self.request_queue):
            req.index = idx
            req.queue_len = len(self.request_queue)
        return curr_req.pop()

    def loop_thread(self):
        with all_logging_disabled():
            import preprocessed
            import comfy.model_management as mm
            from comfy.utils import set_progress_bar_global_hook
            self.NODE_CLASS_MAPPINGS = preprocessed.NODE_CLASS_MAPPINGS
            
        print("Telegram bot running, listening for all commands")
        while True:
            if not self.request_queue: continue
            pbar_message, command_name, orig_message, parsed_data, image_output_callback = self.get_request()
            parsed_data["prompt"] = parsed_data["prompt"].replace("''", '')
            hooks = create_hooks(self, orig_message, parsed_data, image_output_callback)
            try:
                getattr(preprocessed, command_name)(self.NODE_CLASS_MAPPINGS, hooks)
                gc.collect()
                mm.soft_empty_cache()
            except:
                handle_exception(self.bot, orig_message)
            finally:
                set_progress_bar_global_hook(None)
                self.executing_user_id = None