from telebot.handler_backends import BaseMiddleware
from telebot.handler_backends import CancelUpdate
from telebot.types import Message
from backed_bot_utils import get_username

class AntiFlood(BaseMiddleware):
    def __init__(self, bot, start_time, window_limit_sec) -> None:
        self.start_time = start_time
        self.last_time = {}
        self.limit = window_limit_sec
        self.bot = bot
        self.update_types = ['message']

    def pre_process(self, message: Message, data):
        if message.date < self.start_time:
            print(f"Skip message {message.id} from {get_username(message.from_user)} ({message.from_user.id}) for being sended before starting-up")
            return CancelUpdate()
        if not message.from_user.id in self.last_time:
            self.last_time[message.from_user.id] = message.date
            return
        if message.date - self.last_time[message.from_user.id] < self.limit:
            self.bot.send_message(message.chat.id, 'You are making request too often')
            return CancelUpdate()
        self.last_time[message.from_user.id] = message.date

        
    def post_process(self, message, data, exception):
        pass