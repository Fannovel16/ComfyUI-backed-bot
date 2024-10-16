import sys, os
sys.path.insert(0, os.path.realpath(os.path.join(__file__, '..')))
from preprocess import preprocess
import dotenv;dotenv.load_dotenv()
import telebot, os
from worker import ComfyWorker
from backed_bot_utils import parse_command_string
from special_commands import SPECIAL_COMMANDS
import middlewares, time

COMMANDS = preprocess(["AppIO_StringInput", "AppIO_StringOutput", "AppIO_ImageInput", "AppIO_ImageOutput", "AppIO_IntegerInput", "AppIO_IntegerInput", "AppIO_ImageInputFromID"])
COMMANDS.extend(SPECIAL_COMMANDS.keys())
FREE_COMMANDS = ["get_ids"]

bot = telebot.TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], parse_mode=None, use_class_middlewares=True)
bot.setup_middleware(middlewares.AntiFlood(
    bot=bot,
    commands=COMMANDS,
    free_commands=FREE_COMMANDS,
    allowed_chat_ids=os.environ.get("ALLOWED_CHAT_IDS", ''),
    allowed_user_ids=os.environ.get("ALLOWED_USER_IDS", ''),
    start_time=time.time(),
    window_limit_sec=int(os.environ.get("MESSAGE_WINDOW_RATE_LIMIT", '3')),
    temp_message_delay_sec=int(os.environ.get("TEMP_MESSAGE_LIFE", '3'))
))
worker = ComfyWorker(bot)

@bot.message_handler(["get_ids"])
def get_ids(message: telebot.types.Message):
    bot.reply_to(message, f"Chat ID: {message.chat.id}, User ID: {message.from_user.id}")

@bot.message_handler(func=lambda _: True, content_types=["text", "photo"])
def main(message: telebot.types.Message):
    text = message.caption if message.content_type == 'photo' else message.text
    command_name = text.strip().split()[0][1:] # Extract command name without '/'
    parsed_data = parse_command_string(text, command_name)
    if command_name in FREE_COMMANDS: return
    print(f"Executing command {command_name}")
    if command_name in SPECIAL_COMMANDS: SPECIAL_COMMANDS[command_name](bot, message, parsed_data)
    else: worker.execute(command_name, message, parsed_data)

print("Telegram bot running, listening for all commands")
bot.polling()