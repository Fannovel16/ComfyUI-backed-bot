import sys, os
sys.path.insert(0, os.path.realpath(os.path.join(__file__, '..')))
from preprocess import preprocess
import dotenv;dotenv.load_dotenv()
import telebot, os
from worker import ComfyWorker
from utils import get_username
from special_commands import SPECIAL_COMMANDS

COMMANDS = preprocess(["AppIO_StringInput", "AppIO_StringOutput", "AppIO_ImageInput", "AppIO_ImageOutput", "AppIO_IntegerInput", "AppIO_IntegerInput", "AppIO_ImageInputFromID"])
COMMANDS.extend(SPECIAL_COMMANDS.keys())
ALLOWED_CHAT_IDS = os.environ.get("ALLOWED_CHAT_IDS", '')
ALLOWED_USER_IDS = os.environ.get("ALLOWED_USER_IDS", '')

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

bot = telebot.TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], parse_mode=None)
worker = ComfyWorker(bot)

@bot.message_handler(["get_ids"])
def get_ids(message: telebot.types.Message):
    bot.reply_to(message, f"Chat ID: {message.chat.id}, User ID: {message.from_user.id}")

@bot.message_handler(func=lambda _: True, content_types=["text", "photo"])
def main(message: telebot.types.Message):
    text = message.caption if message.content_type == 'photo' else message.text
    if text is None or len(text.strip()) == 0:
        return
    text = text.strip()
    if text[0] != '/': return
    command_name = text.strip().split()[0][1:] # Extract command name without '/'
    if command_name == "get_ids":
        return
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    user_name = get_username(message.from_user)
    if len(ALLOWED_CHAT_IDS.strip()) and chat_id not in ALLOWED_CHAT_IDS:
        print(f"Allowed chatids are: {ALLOWED_CHAT_IDS}, but got message from user: {user_name} ({user_id}), chatid: {chat_id} ! Skipping message.")
        return
    if len(ALLOWED_USER_IDS.strip()) and user_id not in ALLOWED_USER_IDS:
        print(f"Allowed userids are: {ALLOWED_USER_IDS}, but got message from user: {user_name} ({user_id}), chatid: {chat_id} ! Skipping message.")
        return
    
    print(f"Received command from chat_id {message.chat.id}, user {user_name} ({user_id}): {text}")
    if command_name not in COMMANDS:
        print(f"Command {command_name} not defined. Current available commands: {', '.join(COMMANDS)}")
        return
    parsed_data = parse_command_string(text, command_name)
    if command_name in SPECIAL_COMMANDS: SPECIAL_COMMANDS[command_name](bot, message, parsed_data)
    else: worker.execute(command_name, message, parsed_data)

print("Telegram bot running, listening for all commands")
bot.polling()