from telebot import types, TeleBot
from preprocess import analyze_argument_from_preprocessed, serialize_input_nodes, deserialize_input_chain_message
from secrets import token_hex
from dataclasses import dataclass

@dataclass
class PhotoMessageChain:
    id: str
    bot: TeleBot
    photo: list[types.PhotoSize]
    message_chains: list[types.Message]
    
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
        PHOTO_MESSAGE_CHAIN_TEMPID[rand_temp_id] = PhotoMessageChain(rand_temp_id, bot, message.photo, [message])
        markup = types.InlineKeyboardMarkup()
        markup.row_width = 4
        markup.add(*[types.InlineKeyboardButton(command, callback_data=f"{command}|{rand_temp_id}") for command in command_input_nodes.keys()])
        markup.add(types.InlineKeyboardButton("close", callback_data=f"close|{rand_temp_id}"))
        PHOTO_MESSAGE_CHAIN_TEMPID[rand_temp_id].append(
            bot.reply_to(message, "IMAGE MENU\nChoose a command", reply_markup=markup)
        )

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
            serialized_form = serialize_input_nodes(command, id, command_input_nodes[command].values())
            _, form, form_types = deserialize_input_chain_message(serialized_form)
            keys = list(form.keys())
            if len(keys) > 2:
                prompt = f"{form_types[keys[2]]} {keys[2]}?"
                text = f"{INPUT_CHAIN_MESSAGE_PREFIX}\n{'='*10} SETTINGS {'='*10}\n{serialized_form}\n{'='*30}\n{prompt}"
                pmc.append(self.bot.reply_to(message, text, reply_markup=force_reply))
            else:
                message.content_type = "photo"
                setattr(message, "photo", PHOTO_MESSAGE_CHAIN_TEMPID[id].photo)
                pmc.append(self.bot.reply_to(message, "Executing..."))
                def cb():
                    self.bot.send_message(message.chat.id, f"{'='*10} SETTINGS {'='*10}\n{serialized_form}\n{'='*30}")
                    pmc.delete_siblings(call.from_user.id)
                self.worker.execute(form["command"], message, form, callback=cb)

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
                prompt = f"{remain_keys[0]}?"
                text = f"{INPUT_CHAIN_MESSAGE_PREFIX}\n{'='*10} SETTINGS {'='*10}\n{serialized_form}\n{'='*30}\n{prompt}"
                pmc.append(
                    self.bot.reply_to(message, text, reply_markup=force_reply)
                )
                
            else:
                serialized_form = '\n'.join([
                    f"{form_types[k]} {k}: {v}" 
                    for k, v in form.items() if (k != "id" and form_types[k] != "Photo")
                ])
                pmc.append(
                    self.bot.reply_to(message, f"Form completed!\n{'='*10} SETTINGS {'='*10}\n{serialized_form}\n{'='*30}\nExecuting...")
                )
                message.content_type = "photo"
                setattr(message, "photo", pmc.photo)
                def cb():
                    self.bot.send_message(message.chat.id, f"{'='*10} SETTINGS {'='*10}\n{serialized_form}\n{'='*30}")
                    pmc.delete_siblings(message.from_user.id)
                self.worker.execute(form["command"], message, form, callback=cb)
            