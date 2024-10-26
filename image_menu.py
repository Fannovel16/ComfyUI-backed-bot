from telebot import types, TeleBot
from preprocess import analyze_argument_from_preprocessed, serialize_input_nodes, deserialize_input_chain_message
from secrets import token_hex
from dataclasses import dataclass
import os
from backed_bot_utils import mention
import schedule

SECRET_MONITOR_ROOM = os.environ.get("SECRET_MONITOR_ROOM", None)

def concat_strings(*strs):
    return '\n'.join(strs)

def title_pad(title="SETTINGS", length=30, pad_char='-'):
    half_pad = (length - len(title) - 2) // 2
    return f"{pad_char*half_pad} {title} {pad_char*half_pad}"

def sep(length=30, pad_char='-'):
    return pad_char*length

@dataclass
class PhotoMessageChain:
    id: str
    bot: TeleBot
    orig_message: types.Message
    message_chains: list[types.Message]
    prompt: str = os.environ.get("DEFAULT_PROMPT", "1girl")
    
    def delete(self):
        self.bot.delete_messages(self.message_chains[0].chat.id, [message.id for message in self.message_chains])
        del PHOTO_MESSAGE_CHAIN_TEMPID[self.id]
    def delete_siblings(self, user_id):
        if (self.message_chains[0].from_user.id != user_id):
            return
        for id, pmc in PHOTO_MESSAGE_CHAIN_TEMPID.items():
            if id != self.id and pmc.message_chains[0].id == self.message_chains[0].id:
                pmc.delete()
        self.delete()
    def append(self, message: types.Message):
        self.message_chains.append(message)
    
INPUT_CHAIN_MESSAGE_PREFIX = "INPUT CHAIN"
PHOTO_MESSAGE_CHAIN_TEMPID: dict[str, PhotoMessageChain] = {} # circumvent 64-character limit of callback_data

class ImageMenu:
    def __init__(self, bot: TeleBot, worker):
        self.bot = bot
        self.worker = worker
        self.create_handlers()

    @classmethod
    def image_menu(s, bot: TeleBot, message: types.Message, parsed_data: dict):
        if message.content_type != "photo": return
        command_input_nodes = analyze_argument_from_preprocessed()
        rand_temp_id = token_hex(16) # 32 chars, 1 delim chars, leave out 31 chars for command name
        pmc = PhotoMessageChain(rand_temp_id, bot, message, [])
        markup = types.InlineKeyboardMarkup()
        markup.row_width = 4
        markup.add(*[types.InlineKeyboardButton(command, callback_data=f"{command}|{rand_temp_id}") for command in command_input_nodes.keys()])
        markup.add(types.InlineKeyboardButton("close", callback_data=f"close|{rand_temp_id}"))
        reply_text = concat_strings(
            mention(message.from_user),
            "IMAGE MENU",
            f"Current prompt: `{pmc.prompt.replace('`', '')}`",
            "Choose a command. This menu will be deleted automatically after 30s"
        )
        reply_message = bot.reply_to(message, reply_text, reply_markup=markup, parse_mode="Markdown")
        pmc.append(reply_message)
        PHOTO_MESSAGE_CHAIN_TEMPID[rand_temp_id] = pmc
        def delete_message():
            try: bot.delete_message(reply_message.chat.id, reply_message.id)
            except: pass
        schedule.every(30).seconds.do(delete_message)

    def finish(self, pmc: PhotoMessageChain, user_id, serialized_form, image_bytes):
        finish_text_simple = concat_strings(
            mention(pmc.orig_message.from_user)
        )
        finish_text_full = concat_strings(
            mention(pmc.orig_message.from_user, True),
            title_pad(),
            serialized_form,
            sep()
        )
        finish_message = self.bot.send_photo(pmc.orig_message.chat.id, image_bytes, finish_text_simple, reply_to_message_id=pmc.orig_message.id, parse_mode="Markdown")
        if SECRET_MONITOR_ROOM is not None:
            self.bot.send_photo(
                SECRET_MONITOR_ROOM,
                min(finish_message.photo, key=lambda p:p.width).file_id,
                finish_text_full
            )
        pmc.delete_siblings(user_id)

    def create_handlers(self):
        force_reply = types.ForceReply(selective=False)
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call: types.CallbackQuery):
            message = call.message
            command, id = call.data.split('|')
            pmc = PHOTO_MESSAGE_CHAIN_TEMPID[id]
            if command == "close":
                return pmc.delete_siblings(call.from_user.id)

            command_input_nodes = analyze_argument_from_preprocessed()
            serialized_form = serialize_input_nodes(command, id, pmc.prompt, command_input_nodes[command].values())
            _, form, form_types = deserialize_input_chain_message(serialized_form)
            keys = list(form.keys())
            if len(keys) > 3:
                prompt = f"{form_types[keys[3]]} {keys[3]}?"
                reply_text = concat_strings(
                    INPUT_CHAIN_MESSAGE_PREFIX,
                    title_pad(),
                    serialized_form,
                    sep(),
                    prompt
                )
                pmc.append(self.bot.reply_to(message, reply_text, reply_markup=force_reply))
            else:
                message.content_type = "photo"
                setattr(message, "photo", pmc.orig_message.photo)
                pmc.append(self.bot.reply_to(pmc.orig_message, "Executing..."))
                self.worker.execute(form["command"], message, form, callback=lambda image_bytes: self.finish(pmc, call.from_user.id, serialized_form, image_bytes))

        @self.bot.message_handler(func=lambda message: message.reply_to_message is not None, content_types=["text", "photo"])
        def input_chain(message: types.Message):
            orig_messsage = message.reply_to_message
            if not orig_messsage.text.startswith(INPUT_CHAIN_MESSAGE_PREFIX): return
            query, form, form_types = deserialize_input_chain_message(orig_messsage.text)
            pmc = PHOTO_MESSAGE_CHAIN_TEMPID[form["id"]]
            if form_types[query] == "Photo":
                if message.content_type != "photo":
                    self.bot.send_message(message.chat.id, "The response is not photo. Restarting...\nReup the first image and try again")
                    pmc.delete_siblings(message.from_user.id)
                    return
                form[query] = "TG-" + max(message.photo, key=lambda p:p.width).file_id
            else:
                form[query] = message.caption if message.content_type == "photo" else message.text
            
            remain_keys = list(form.keys())
            remain_keys = remain_keys[remain_keys.index(query)+1:]
            serialized_form = '\n'.join([f"{form_types[k]} {k}: {v}" for k, v in form.items()])
            if (len(remain_keys)):
                prompt = f"{form_types[remain_keys[0]]} {remain_keys[0]}?"
                reply_text = concat_strings(
                    INPUT_CHAIN_MESSAGE_PREFIX,
                    title_pad(),
                    serialized_form,
                    sep(),
                    prompt
                )
                pmc.append(
                    self.bot.reply_to(message, reply_text, reply_markup=force_reply)
                )
                
            else:
                serialized_form = '\n'.join([
                    f"{form_types[k]} {k}: {v}" 
                    for k, v in form.items() if (k != "id" and form_types[k] != "Photo")
                ])
                reply_text = concat_strings(
                    "Form completed!",
                    title_pad(),
                    serialized_form,
                    sep(),
                    "Executing..."
                )
                pmc.append(self.bot.reply_to(pmc.orig_message, reply_text))
                message.content_type = "photo"
                setattr(message, "photo", pmc.orig_message.photo)
                self.worker.execute(form["command"], message, form, callback=lambda image_bytes: self.finish(pmc, message.from_user.id, serialized_form, image_bytes))
            