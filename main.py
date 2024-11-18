import sys, os
sys.path.insert(0, os.path.realpath(os.path.join(__file__, '..')))
import dotenv;dotenv.load_dotenv()

from preprocess import preprocess
from worker import ComfyWorker
from backed_bot_utils import parse_command_string, handle_exception
from special_commands import SPECIAL_COMMANDS
from telebot import types, TeleBot, logger, logging, ExceptionHandler
from image_menu import ImageMenu
import middlewares, time
from auth_manager import warmup

# Disabled by default as the the contractor deems unnecessary
ENABLE_COMMANDS = int(os.environ.get("ENABLE_COMMANDS", "0"))
COMMANDS = preprocess(
    ["AppIO_StringInput", "AppIO_StringOutput", "AppIO_ImageInput", "AppIO_ImageOutput", "AppIO_IntegerInput", "AppIO_IntegerInput", "AppIO_ImageInputFromID"]
    + [el.strip() for el in os.environ.get("NODES_TO_CACHE", '').split(',')]
)
if not ENABLE_COMMANDS:
    COMMANDS = []
COMMANDS.extend(SPECIAL_COMMANDS.keys())
COMMANDS.extend(["image_menu"])
FREE_COMMANDS = ["get_ids"]
warmup()

if int(os.environ.get("TELEBOT_DEBUG", "0")):
    logger.setLevel(logging.DEBUG)

class MyExceptionHandler(ExceptionHandler):
    def handle(self, exception):
        #logging.error(exception)
        handle_exception(bot)

bot = TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], parse_mode=None, use_class_middlewares=True, exception_handler=MyExceptionHandler())
bot.setup_middleware(middlewares.get_anti_flood(
    bot=bot,
    commands=COMMANDS,
    free_commands=FREE_COMMANDS,
    allowed_chat_ids=os.environ.get("ALLOWED_CHAT_IDS", '*'),
    start_time=time.time(),
    window_limit_sec=int(os.environ.get("MESSAGE_WINDOW_RATE_LIMIT", '5')),
    temp_message_delay_sec=int(os.environ.get("TEMP_MESSAGE_LIFE", '5'))
))
worker = ComfyWorker(bot)
image_menu = ImageMenu(bot, worker)
SPECIAL_COMMANDS["image_menu"] = image_menu.image_menu

@bot.message_handler(["get_ids"])
def get_ids(message: types.Message):
    _message = message.reply_to_message or message
    bot.reply_to(message, f"Chat ID: {_message.chat.id}, User ID: {_message.from_user.id}")

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

bot.infinity_polling()
