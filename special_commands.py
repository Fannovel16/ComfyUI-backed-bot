from telebot import types, TeleBot
from telebot import types
from backed_bot_utils import telegram_reply_to, handle_exception, get_username, get_dbm
from PIL import Image
from io import BytesIO
import os
from auth_manager import AuthManager, ComfyCommandManager

ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", '')
IMAGE_FORMAT = os.environ.get("IMAGE_FORMAT", "png").upper()

def get_full_image_id(user_id, image_id):
    return f"{user_id}:{image_id}"

def set_image_id(bot: TeleBot, message: types.Message, parsed_data: dict):
    if message.content_type != "photo":
        return telegram_reply_to(bot, message, "Command set_image_id expects a photo")
    _image_id = parsed_data.get("prompt", '') or ''
    if len(_image_id.strip()) == 0:
        return telegram_reply_to(bot, message, "Image id must not be empty for this command")
    try:
        with get_dbm("image_ids") as image_ids:
            full_image_id = get_full_image_id(message.from_user.id, _image_id)
            file_id = max(message.photo, key=lambda p:p.width).file_id
            image_ids[full_image_id] = file_id
        return telegram_reply_to(bot, message, f"Image id {_image_id} is set successfully")
    except:
        handle_exception(bot, message)

def get_image_id(bot: TeleBot, message: types.Message, parsed_data: dict):
    _image_id = parsed_data.get("prompt", '') or ''
    if len(_image_id.strip()) == 0:
        return telegram_reply_to(bot, message, "Image id must not be empty for this command")
    try:
        with get_dbm("image_ids") as image_ids:
            full_image_id = get_full_image_id(message.from_user.id, _image_id)
            if full_image_id not in image_ids:
                return telegram_reply_to(bot, message, f"Image_id {_image_id} isn't set for user {get_username(message.from_user)} ({message.from_user.id}). Run `/set_image_id {_image_id}` with a photo")
            file_id = image_ids[full_image_id]
            telegram_reply_to(bot, message, f"Found image id {_image_id}. Wait a second")
        
        file_info = bot.get_file(file_id)
        image_pil = Image.open(BytesIO(bot.download_file(file_info.file_path)))
        image_bytes = BytesIO()
        image_pil.save(image_bytes, format=IMAGE_FORMAT)
        image_bytes.seek(0)
        telegram_reply_to(bot, message, image_bytes)
    except:
        handle_exception(bot, message)    

SPECIAL_COMMANDS = {
    "set_image_id": set_image_id,
    "get_image_id": get_image_id,
    "get_allowed": AuthManager.get_allowed,
    "add_allowed": AuthManager.add_allowed,
    "remove_allowed": AuthManager.remove_allowed,
    "add_advanced": AuthManager.add_advanced,
    "remove_advanced": AuthManager.remove_advanced,
    "set_normal_uses": AuthManager.set_normal_uses,
    "get_commands": ComfyCommandManager.get_commands,
    "set_commands": ComfyCommandManager.set_commands
}