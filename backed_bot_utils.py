import telebot, typing
import datetime, traceback
from pathlib import Path
from io import BytesIO
import dbm

def get_username(user: telebot.types.User):
    if user.username is not None: 
        return user.username
    return user.first_name

def telegram_reply_to(bot: telebot.TeleBot, message: telebot.types.Message, text_or_photo: typing.Union[str, BytesIO]):
    full_command = message.caption if message.content_type == 'photo' else message.text
    if full_command is None: full_command = ''
    if isinstance(text_or_photo, str):
        text = text_or_photo
        try: bot.reply_to(message, text)
        except: bot.send_message(
            message.chat.id,
            f"[@{get_username(message.from_user)}](tg://user?id={message.from_user.id}) Result of `{full_command}`:\n{text}", 
            parse_mode="Markdown"
        )
    else:
        photo = text_or_photo
        try: bot.send_photo(message.chat.id, photo, reply_to_message_id=message.id)
        except:
            try: bot.send_photo(
                message.chat.id, 
                photo, 
                caption=f"[@{get_username(message.from_user)}](tg://user?id={message.from_user.id}) Result of `{full_command}`", 
                parse_mode="Markdown"
            )
            except: bot.send_message(
                message.chat.id, 
                f"[@{get_username(message.from_user)}](tg://user?id={message.from_user.id}) Can't get image from `{full_command}`. Try again.", 
                parse_mode="Markdown"
            )

def handle_exception(bot: telebot.TeleBot, original_message: telebot.types.Message, e: Exception, full_traceback: str):
    utc_time = datetime.datetime.now(datetime.timezone.utc)
    date_str = utc_time.strftime("%d-%m-%Y_%H.%M.%S")
    error_log_dir = Path(__file__, '..', 'error_logs').resolve()
    error_log_dir.mkdir(exist_ok=True)
    
    Path(error_log_dir, f"{date_str} {original_message.chat.id} {original_message.from_user.id}.txt") \
        .resolve() \
        .write_text(traceback.format_exc(), encoding="utf-8")
    telegram_reply_to(bot, original_message, f"Error: {str(e)}")

def get_dbm(db_name):
    dbm_dir = Path(__file__).parent / "dbm_data"
    dbm_dir.mkdir(exist_ok=True)
    return dbm.open(Path(dbm_dir / db_name).resolve(), 'c')

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
