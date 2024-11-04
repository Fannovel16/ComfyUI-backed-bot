from telebot import types, TeleBot
from telebot import types
from backed_bot_utils import telegram_reply_to, handle_exception, get_username, get_dbm
from PIL import Image
from io import BytesIO
import os

ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", '')

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
        image_pil.save(image_bytes, format="JPEG", quality="web_high")
        image_bytes.seek(0)
        telegram_reply_to(bot, message, image_bytes)
    except:
        handle_exception(bot, message)

def get_allowed(bot: TeleBot, message: types.Message, parsed_data: dict):
    if str(message.from_user.id) != ADMIN_USER_ID:
        print(f"User {get_username(message.from_user)} ({message.from_user.id}) is not permited to get allowed users")
        return
    with get_dbm("allowed_users") as allowed_users:
        if allowed_users:
            user_bullet_list = "\n".join(
                f"• `{user_name.decode()}` ({user_id.decode()})"
                for user_id, user_name in allowed_users.items()
            )
            bot.reply_to(message, f"Allowed users:\n{user_bullet_list}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "No user is allowed to use this bot yet")

def add_allowed(bot: TeleBot, message: types.Message, parsed_data: dict):
    if str(message.from_user.id) != ADMIN_USER_ID:
        print(f"User {get_username(message.from_user)} ({message.from_user.id}) is not permitted to add allowed users")
        return
    with get_dbm("allowed_users") as allowed_users:
        user_id_names = map(
            lambda s: s.split('/') if '/' in s else (s, "Everyone" if s.strip() == '*' else "Name_Unknown"),
            parsed_data["prompt"].split(',')
        )
        users = {user_id.strip(): user_name.replace('`', '').strip() for user_id, user_name in user_id_names}
        allowed_users.update(users)
        user_bullet_list = "\n".join(
            f"• `{user_id.decode()}` (`{user_name.decode()}`)"
            for user_id, user_name in allowed_users.items()
        )
        bot.reply_to(message, f"Allowed users:\n{user_bullet_list}", parse_mode="Markdown")

def remove_allowed(bot: TeleBot, message: types.Message, parsed_data: dict):
    if str(message.from_user.id) != ADMIN_USER_ID:
        print(f"User {get_username(message.from_user)} ({message.from_user.id}) is not permitted to remove allowed users")
        return
    text = "Removed successfully"
    with get_dbm("allowed_users") as allowed_users:
        if parsed_data["prompt"].strip() == "everyone":
            users = {user_id.decode(): user_name.decode() for user_id, user_name in allowed_users.items()}
        else:
            users = map(lambda s: s.strip(), parsed_data["prompt"].split(','))
        for user_id in users:
            if user_id.encode() in allowed_users and user_id != ADMIN_USER_ID:
                del allowed_users[user_id.encode()]
        
        if allowed_users:
            user_bullet_list = "\n".join(
                f"• `{user_id.decode()}` (`{user_name.decode()}`)"
                for user_id, user_name in allowed_users.items()
            )
            text += f"\nAllowed users:\n{user_bullet_list}"
        else:
            text += "\nNo user is allowed to use this bot yet"
    bot.reply_to(message, text, parse_mode="Markdown")


SPECIAL_COMMANDS = {
    "set_image_id": set_image_id,
    "get_image_id": get_image_id,
    "get_allowed": get_allowed,
    "add_allowed": add_allowed,
    "remove_allowed": remove_allowed
}