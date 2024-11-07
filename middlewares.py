from telebot.handler_backends import BaseMiddleware, CancelUpdate, ContinueHandling
from telebot import types, TeleBot
from backed_bot_utils import get_username
import schedule
import os
from auth_manager import AuthManager, UserInfo

ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", '')

class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self, bot, commands, free_commands, allowed_chat_ids, start_time, window_limit_sec, temp_message_delay_sec) -> None:
        self.start_time = start_time
        self.last_time = {}
        self.limit = window_limit_sec
        self.bot: TeleBot = bot
        self.allowed_chat_ids = allowed_chat_ids
        self.update_types = ['message']
        self.commands = commands
        self.free_commands = free_commands
        self.temp_message_delay_sec = temp_message_delay_sec

    def authenticate(self, message: types.Message):
        user_name = get_username(message.from_user)
        user_id = str(message.from_user.id)
        chat_id = str(message.chat.id)
        is_private_chat = message.chat.type == "private"
        with AuthManager.allowed_user_dbm() as allowed_users:
            user_info: UserInfo = allowed_users.get(user_id, None)
            if user_info is not None and user_info.name == "Name_Unknown":
                user_info.name = user_name
            
            if user_info is not None and not user_info.is_allowed:
                print(f"User {user_id} is banned! Skipping message")
                return False
            
            if is_private_chat:
                if user_info is None or user_info.advanced_info is None:
                    print(f"User {user_name} ({user_id}) is not advanced. Skipping direct message")
                    return False
                else:
                    print(f"User {user_name} ({user_id}) is advanced")
                    return True
            
            if chat_id not in self.allowed_chat_ids:
                print(f"Allowed chatids are: {list(self.allowed_chat_ids)}, but got message from user: {user_name} ({user_id}), chatid: {chat_id} ! Skipping message")
                return False

            if '*' in allowed_users:
                return True
            if user_id not in allowed_users:
                print(f"Allowed userids are: {list(allowed_users.keys())}, but got message from user: {user_name} ({user_id}), chatid: {chat_id} ! Skipping message.")
                return False
    
    def check(self, user_id, message):
        if not user_id in self.last_time:
            self.last_time[user_id] = (message.date, None)
            return ContinueHandling()
        
        last_message_date, notify_message_date = self.last_time[user_id]
        if message.date - last_message_date < self.limit:
            if (notify_message_date is None) or (message.date - notify_message_date > self.temp_message_delay_sec):
                print(f"User {user_id} are spamming")
                notify_message = self.bot.send_message(message.chat.id, f"You are spamming commands. Wait for {self.limit} seconds")
                def delete_message():
                    try: self.bot.delete_message(notify_message.chat.id, notify_message.id)
                    except: pass
                    return schedule.CancelJob
                notify_message_date = notify_message.date
                schedule.every(self.temp_message_delay_sec).seconds.do(delete_message)
            self.last_time[user_id] = (message.date, notify_message_date)
            return CancelUpdate()
        else:
            self.last_time[user_id] = (message.date, notify_message_date)
            return ContinueHandling()
    
    def get_command(self, text):
        if text is None or type(text) != str or len(text.strip()) == 0:
            return None
        if text[0] != '/':
            return None
        return text.strip().split()[0][1:] # Extract command name without '/'

    def pre_process(self, message: types.Message, data):
        user_id = str(message.from_user.id)
        user_name = get_username(message.from_user)
        is_allowed = self.authenticate(message)
        text = message.caption if message.content_type == 'photo' else message.text
        command = self.get_command(text)

        if message.date < self.start_time:
            print(f"Skip message {message.id} from {user_name} ({user_id}) for being sended before starting-up")
            return CancelUpdate()
        
        if message.content_type == 'photo':
            if is_allowed:
                return ContinueHandling()
            else:
                return CancelUpdate()
        
        print(f"Received command from chat_id {message.chat.id}, user {user_name} ({user_id}): {text}")
        if command in self.free_commands:
            return ContinueHandling()
        if command is None:
            if message.reply_to_message is not None and message.reply_to_message.from_user.id == self.bot.user.id:
                return ContinueHandling()
            else:
                return CancelUpdate()
        if command not in self.commands:
            print(f"Command {command} not defined. Current available commands: {', '.join(self.commands)}")
            return CancelUpdate()
        return self.check(user_id, message)
        
    def post_process(self, message, data, exception):
        pass

anti_flood = None
def get_anti_flood(*args, **kwargs):
    global anti_flood
    if anti_flood is None:
        if len(args) == 0 and len(kwargs) == 0:
            raise RuntimeError("Anti flood middleware is not initialized, got zero argument")
        anti_flood = AntiFloodMiddleware(*args, **kwargs)
    return anti_flood
