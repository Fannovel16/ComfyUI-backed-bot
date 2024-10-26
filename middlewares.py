from telebot.handler_backends import BaseMiddleware, CancelUpdate, ContinueHandling
from telebot import types, TeleBot
from backed_bot_utils import get_username
import schedule

class AntiFlood(BaseMiddleware):
    def __init__(self, bot, commands, free_commands, allowed_chat_ids, allowed_user_ids, start_time, window_limit_sec, temp_message_delay_sec) -> None:
        self.start_time = start_time
        self.last_time = {}
        self.limit = window_limit_sec
        self.bot: TeleBot = bot
        self.allowed_chat_ids = allowed_chat_ids
        self.allowed_user_ids = allowed_user_ids
        self.update_types = ['message']
        self.commands = commands
        self.free_commands = free_commands
        self.temp_message_delay_sec = temp_message_delay_sec

    def authenticate(self, chat_id, user_id, user_name):
        if len(self.allowed_chat_ids.strip()) and chat_id not in self.allowed_chat_ids:
            print(f"Allowed chatids are: {self.allowed_chat_ids}, but got message from user: {user_name} ({user_id}), chatid: {chat_id} ! Skipping message.")
            return False
        if len(self.allowed_user_ids.strip()) and user_id not in self.allowed_user_ids:
            print(f"Allowed userids are: {self.allowed_user_ids}, but got message from user: {user_name} ({user_id}), chatid: {chat_id} ! Skipping message.")
            return False
        return True
    
    def check_flood(self, user_id, message):
        if not user_id in self.last_time:
            self.last_time[user_id] = (message.date, None)
            return ContinueHandling()
        
        last_message_date, notify_message_date = self.last_time[user_id]
        if message.date - last_message_date < self.limit:
            if (notify_message_date is None) or (message.date - notify_message_date > self.temp_message_delay_sec):
                notify_message = self.bot.send_message(message.chat.id, f"You are spamming requests. Wait for {self.limit} seconds")
                def delete_message():
                    try: self.bot.delete_message(notify_message.chat.id, notify_message.id)
                    except: pass
                notify_message_date = notify_message.date
                schedule.every(self.temp_message_delay_sec).seconds.do(delete_message)
            self.last_time[user_id] = (message.date, notify_message_date)
            return CancelUpdate()
        else:
            self.last_time[user_id] = (message.date, notify_message_date)
            return ContinueHandling()

    def pre_process(self, message: types.Message, data):
        chat_id = str(message.chat.id)
        user_id = str(message.from_user.id)
        user_name = get_username(message.from_user)
        
        if message.date < self.start_time:
            print(f"Skip message {message.id} from {user_name} ({user_id}) for being sended before starting-up")
            return CancelUpdate()
        
        if not self.authenticate(chat_id, user_id, user_name):
            return CancelUpdate()

        if message.reply_to_message is not None and message.reply_to_message.from_user.id == self.bot.user.id:
            return ContinueHandling()
        
        text = message.caption if message.content_type == 'photo' else message.text
        if text is None or len(text.strip()) == 0:
            return ContinueHandling() if message.content_type == 'photo' else CancelUpdate()
        text = text.strip()
        if text[0] != '/': return CancelUpdate()
        command_name = text.strip().split()[0][1:] # Extract command name without '/'
        if command_name in self.free_commands:
            return ContinueHandling()
        
        print(f"Received command from chat_id {message.chat.id}, user {user_name} ({user_id}): {text}")
        if command_name not in self.commands:
            print(f"Command {command_name} not defined. Current available commands: {', '.join(self.commands)}")
            return CancelUpdate()

        return self.check_flood(user_id, message)
        
    def post_process(self, message, data, exception):
        pass