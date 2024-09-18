import sys, os
sys.path.insert(0, os.path.realpath(os.path.join(__file__, '..')))
from preprocess import preprocess
import dotenv;dotenv.load_dotenv()
import telebot, os
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
import torch
import numpy as np
from io import BytesIO

ALLOWED_CHAT_IDS = os.environ.get("ALLOWED_CHAT_IDS", '')
COMMANDS = preprocess(["AppIO_StringInput", "AppIO_StringOutput", "AppIO_ImageInput", "AppIO_ImageOutput", "AppIO_IntegerInput", "AppIO_IntegerInput"])
import preprocessed
import comfy.model_management as mm
import gc

def parse_command_string(command_string, command_name):
    textAndArgs = command_string[1+ len(command_name):].strip().split('--')
    result = {}
    text = textAndArgs[0].strip()
    args = textAndArgs[1:]
    print(args)
    # The first element is the "freeText" part, remove any leading or trailing whitespace.
    result["prompt"] = text.strip()

    for arg in args:
        parts = arg.split()
        if len(parts) > 1:
            # Extract the argument name and value
            arg_name = parts[0].strip()
            arg_value = ' '.join(parts[1:]).strip()
            result[arg_name] = arg_value

    return result

def get_hooks(message, parsed_data):
    global bot
    def handle_string_input(required, string, argument_name):
        if argument_name not in parsed_data:
            raise RuntimeError(f"Missing argument --{argument_name}")
        if required and argument_name not in parsed_data:
            if argument_name == "prompt": raise RuntimeError("A prompt is required")
            else: raise RuntimeError(f"Argument --{argument_name} is required")
        return (parsed_data.get(argument_name, string),)
    
    def handle_string_output(string):
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
    

bot = telebot.TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], parse_mode=None) 

@bot.message_handler(func=lambda _: True, content_types=["text", "photo"])
def main(message):
    chat_id = str(message.chat.id)
    if len(ALLOWED_CHAT_IDS.strip()) and chat_id not in ALLOWED_CHAT_IDS:
        print(f"Allowed chatids are: {ALLOWED_CHAT_IDS}, but got message from user: {message.from_user.username}, chatid: {chat_id} ! Skipping message.")
        return
    text = message.caption if message.content_type == 'photo' else message.text
    command_name = text.strip().split()[0][1:] # Extract command name without '/'
    print(f"Received command from {message.chat.id}: {text}")
    if command_name not in COMMANDS:
        print(f"Command {command_name} not defined. Current available commands: {', '.join(COMMANDS)}")
        return
    try:
        parsed_data = parse_command_string(text, command_name)
        hooks = get_hooks(message, parsed_data)
        getattr(preprocessed, command_name)(hooks)
        mm.cleanup_models()
        gc.collect()
        mm.soft_empty_cache()
    except:
        import traceback
        bot.reply_to(message, traceback.format_exc())

print("Telegram bot running, listening for all commands")
bot.polling()