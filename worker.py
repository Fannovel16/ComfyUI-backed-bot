from preprocess import preprocess
import dotenv;dotenv.load_dotenv()
import os
from types import SimpleNamespace
from PIL import Image
import torch
import numpy as np
from io import BytesIO
from collections import deque
import traceback, datetime
from pathlib import Path
import threading

ALLOWED_CHAT_IDS = os.environ.get("ALLOWED_CHAT_IDS", '')
COMMANDS = preprocess(["AppIO_StringInput", "AppIO_StringOutput", "AppIO_ImageInput", "AppIO_ImageOutput", "AppIO_IntegerInput", "AppIO_IntegerInput"])
import preprocessed
import comfy.model_management as mm
import gc

def create_hooks(bot, message, parsed_data):
    def handle_string_input(required, string, argument_name):
        if required and argument_name not in parsed_data:
            if argument_name == "prompt": raise RuntimeError("A prompt is required")
            else: raise RuntimeError(f"Argument --{argument_name} is required")
        return (parsed_data.get(argument_name, string),)
    
    def handle_string_output(string):
        if len(string.strip()) == 0:
            raise RuntimeError(f"String passed to StringOutput node must not be empty")
        bot.reply_to(message, string)

    def handle_image_input(**kwargs):
        if message.content_type != "photo":
            raise RuntimeError(f"This command requires an image")
        file_info = bot.get_file(max(message.photo, key=lambda p:p.width).file_id)
        img = Image.open(BytesIO(bot.download_file(file_info.file_path)))
        return (torch.from_numpy(np.array(img)[:, :, :3]/255.).unsqueeze(0),)
    
    def handle_image_output(image):
        image_pil = Image.fromarray(image[0, :, :, :3].cpu().numpy().__mul__(255.).astype(np.uint8))
        image_bytes = BytesIO()
        image_pil.save(image_bytes, format="PNG")
        image_bytes.seek(0)
        bot.send_photo(message.chat.id, image_bytes, reply_to_message_id=message.id)
    
    def handle_integer_input(required, integer, integer_min, integer_max, argument_name):
        if argument_name not in parsed_data:
            raise RuntimeError(f"Missing argument {argument_name}")
        if required and argument_name not in parsed_data:
            raise RuntimeError(f"Argument --{argument_name} is required")
        warning_msg = ''
        if integer < integer_min:
            warning_msg += f"The minium of --{argument_name} is {integer_min}. Changing to that value\n"
        if integer > integer_min:
            warning_msg += f"The minium of --{argument_name} is {integer_min}. Changing to that value\n"
        if len(warning_msg): bot.reply_to(message, warning_msg)
        integer = int(parsed_data.get(argument_name, integer))
        integer = max(integer_max, min(integer, integer_min))
        return (integer,)

    return {
        "AppIO_StringInput": SimpleNamespace(execute=handle_string_input),
        "AppIO_StringOutput": SimpleNamespace(execute=handle_string_output),
        "AppIO_ImageInput": SimpleNamespace(execute=handle_image_input),
        "AppIO_ImageOutput": SimpleNamespace(execute=handle_image_output),
        "AppIO_IntegerInput": SimpleNamespace(execute=handle_integer_input),
    }

class ComfyWorker:
    def __init__(self, bot):
        self.data = deque()
        self.bot = bot
        threading.Thread(target=self.loop_thread, daemon=True).start()
    
    def execute(self, command_name, message, parsed_data):
        self.data.append((command_name, message, parsed_data))

    def loop_thread(self):
        while True:
            if not self.data: continue
            command_name, message, parsed_data = self.data.popleft()
            hooks = create_hooks(self.bot, message, parsed_data)
            try:
                getattr(preprocessed, command_name)(hooks)
                mm.cleanup_models()
                gc.collect()
                mm.soft_empty_cache()
            except Exception as e:
                utc_time = datetime.datetime.now(datetime.timezone.utc)
                date_str = utc_time.strftime("%d-%m-%Y_%H.%M.%S")
                error_log_dir = Path(__file__, '..', 'error_logs').resolve()
                error_log_dir.mkdir(exist_ok=True)
                
                Path(error_log_dir, f"{date_str} {message.chat.id} {message.from_user.id}.txt") \
                    .resolve() \
                    .write_text(f"Error: {traceback.format_exc()}", encoding="utf-8")
                self.bot.reply_to(message, str(e))
    