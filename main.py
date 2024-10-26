import sys, os
sys.path.insert(0, os.path.realpath(os.path.join(__file__, '..')))
import dotenv;dotenv.load_dotenv()

from preprocess import preprocess
from worker import ComfyWorker
from backed_bot_utils import parse_command_string
from special_commands import SPECIAL_COMMANDS
from telebot import types, TeleBot, logger, logging
from image_menu import ImageMenu
import middlewares, time, threading, schedule

COMMANDS = preprocess(["AppIO_StringInput", "AppIO_StringOutput", "AppIO_ImageInput", "AppIO_ImageOutput", "AppIO_IntegerInput", "AppIO_IntegerInput", "AppIO_ImageInputFromID"])
COMMANDS.extend(SPECIAL_COMMANDS.keys())
COMMANDS.extend(["image_menu"])
FREE_COMMANDS = ["get_ids"]
# Disabled by default as the the contractor deems unnecessary
ENABLE_COMMANDS = int(os.environ.get("ENABLE_COMMANDS", "0"))

def remove_message_loop():
    cease_continuous_run = threading.Event()
    class ScheduleThread(threading.Thread):
        @classmethod
        def run(cls):
            while not cease_continuous_run.is_set():
                schedule.run_pending()
                time.sleep(1)

    continuous_thread = ScheduleThread()
    continuous_thread.start()

remove_message_loop()
logger.setLevel(logging.DEBUG)
bot = TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], parse_mode=None, use_class_middlewares=True)
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
image_menu = ImageMenu(bot, worker)
SPECIAL_COMMANDS["image_menu"] = ImageMenu.image_menu

@bot.message_handler(["get_ids"])
def get_ids(message: types.Message):
    bot.reply_to(message, f"Chat ID: {message.chat.id}, User ID: {message.from_user.id}")

@bot.message_handler(func=lambda message: message.reply_to_message is None, content_types=["text", "photo"])
def main(message: types.Message):
    text = message.caption if message.content_type == 'photo' else message.text
    if (message.content_type == 'photo') and (text is None or len(text.strip()) == 0):
        SPECIAL_COMMANDS["image_menu"](bot, message, {"prompt": ''})
        return
    command_name = text.strip().split()[0][1:] # Extract command name without '/'
    parsed_data = parse_command_string(text, command_name)
    if command_name in FREE_COMMANDS: return
    print(f"Executing command {command_name}")
    if command_name in SPECIAL_COMMANDS: SPECIAL_COMMANDS[command_name](bot, message, parsed_data)
    elif ENABLE_COMMANDS:
        worker.execute(command_name, message, parsed_data)

bot.polling()