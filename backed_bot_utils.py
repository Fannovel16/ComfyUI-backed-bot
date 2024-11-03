import typing
import traceback
from pathlib import Path
from io import BytesIO, StringIO
import dbm, os
from telebot import TeleBot, types, logger
from contextlib import contextmanager
import logging, unicodedata, threading, schedule, time
from datetime import datetime, timezone, timedelta
from typing import Optional

TIMEZONE_DELTA = float(os.environ.get("TIMEZONE_DELTA", "7"))
LOG_CAPTURE = StringIO()
ch = logging.StreamHandler(LOG_CAPTURE)
ch.setLevel(logging.DEBUG)

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[ch],
    force=True
)

def get_username(user: types.User):
    name = None
    if user.username is not None: name = user.username
    else: name = user.full_name
    if name is None: name = "Anonymous"
    name = unicodedata.normalize('NFKD', name) \
        .encode('ascii', 'ignore').decode("ascii") \
        .replace('[', '').replace(']', '').replace("@", '')
    return name

def mention(user: types.User, display_user_id=False):
    if display_user_id:
        return f"[@{get_username(user)}](tg://user?id={user.id}) (user id: {user.id})"
    else:
        return f"[@{get_username(user)}](tg://user?id={user.id})"

def telegram_reply_to(bot: TeleBot, message: types.Message, text_or_photo: typing.Union[str, BytesIO]):
    full_command = message.caption if message.content_type == 'photo' else message.text
    if full_command is None: full_command = ''
    if isinstance(text_or_photo, str):
        text = text_or_photo
        try: return bot.reply_to(message, text)
        except: pass
        try:
            return bot.send_message(
                message.chat.id,
                f"{mention(message.from_user)} `{text}`", 
                parse_mode="Markdown"
            )
        except: pass
    else:
        photo = text_or_photo
        try:
            return bot.send_photo(message.chat.id, photo, reply_to_message_id=message.id)
        except: pass
        try: 
            return bot.send_photo(
                message.chat.id, 
                photo, 
                caption=mention(message.from_user), 
                parse_mode="Markdown"
            )
        except: pass
        try:
            return bot.send_message(
                message.chat.id, 
                f"{mention(message.from_user)} Can't get image from `{full_command}`. Try again.", 
                parse_mode="Markdown"
            )
        except: pass

def handle_exception(bot: TeleBot, orig_message: Optional[types.Message] = None):
    utc_time = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=TIMEZONE_DELTA)))
    date_str = utc_time.strftime("%d-%m-%Y_%H.%M.%S")
    error_log_dir = Path(__file__, '..', 'error_logs').resolve()
    error_log_dir.mkdir(exist_ok=True)

    if orig_message is None:
        Path(error_log_dir, f"{date_str}.txt") \
        .resolve() \
        .write_text(
            traceback.format_exc() \
            + f"\n\nTeleBot's logging: \n{LOG_CAPTURE.getvalue()}",
            encoding="utf-8"
        )
        print(f"Connection error! See {date_str}.txt for more details")
    else:
        Path(error_log_dir, f"{date_str}.txt") \
            .resolve() \
            .write_text(
                f"In {orig_message.chat.type} chat {orig_message.chat.title or ''} ({orig_message.chat.id}), user @{get_username(orig_message.from_user)} ({orig_message.from_user.id}) got error:\n" \
                + traceback.format_exc() \
                + f"\n\nTeleBot's logging: \n{LOG_CAPTURE.getvalue()}",
                encoding="utf-8"
            )
        print(f"Error during command execution. See {date_str} for more details")
        telegram_reply_to(bot, orig_message, f"Error ({date_str}). Please retry again")
    
    LOG_CAPTURE.truncate(0)
    LOG_CAPTURE.seek(0)

dbm_locks = {}
def get_dbm(db_name):
    if db_name not in dbm_locks:
        dbm_locks[db_name] = threading.Lock()
    with dbm_locks[db_name]:
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

# Sauce: https://gist.github.com/simon-weber/7853144
@contextmanager
def all_logging_disabled(highest_level=logging.CRITICAL):
    """
    A context manager that will prevent any logging messages
    triggered during the body from being processed.
    :param highest_level: the maximum logging level in use.
      This would only need to be changed if a custom level greater than CRITICAL
      is defined.
    """
    # two kind-of hacks here:
    #    * can't get the highest logging level in effect => delegate to the user
    #    * can't get the current module-level override => use an undocumented
    #       (but non-private!) interface

    previous_level = logging.root.manager.disable

    logging.disable(highest_level)

    try:
        yield
    finally:
        logging.disable(previous_level)

def start_schedule_thread():
    cease_continuous_run = threading.Event()
    def loop():
        while not cease_continuous_run.is_set():
            schedule.run_pending()
            time.sleep(1)
    threading.Thread(target=loop).start()
