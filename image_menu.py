from telebot import types, TeleBot
from preprocess import analyze_argument_from_preprocessed, serialize_input_nodes, deserialize_input_chain_message
from dataclasses import dataclass
from backed_bot_utils import mention, get_username
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, Future
from queue import Queue
import os, schedule, time, middlewares

SECRET_MONITOR_ROOM = os.environ.get("SECRET_MONITOR_ROOM", None)

def concat_strings(*strs):
    return '\n'.join(strs)

def title_pad(title="SETTINGS", length=30, pad_char='-'):
    half_pad = (length - len(title) - 2) // 2
    return f"\n```\n{pad_char*half_pad} {title} {pad_char*half_pad}"

def sep(length=30, pad_char='-'):
    return pad_char*length + "\n```"

# Group rate limit is 20 messages/mintute
class DelayedExecutor:
    def __init__(self, delay=5):
        self.delay = delay
        self.executor = ThreadPoolExecutor(max_workers=2)

    def __call__(self, chat: types.Chat, func):
        if chat.type == "private":
            return func()

        def delayed_task():
            time.sleep(self.delay)
            return func()

        future = self.executor.submit(delayed_task)
        return future.result()

@dataclass
class PhotoMessageChain:
    id: str
    bot: TeleBot
    orig_message: types.Message
    message_chains: list[types.Message]
    auto_close_job: schedule.Job = None
    prompt: str = os.environ.get("DEFAULT_PROMPT", "1girl")
    
    def delete(self):
        if len(self.message_chains):
            self.bot.delete_messages(self.orig_message.chat.id, [message.id for message in self.message_chains])

    def append(self, message: types.Message):
        self.message_chains.append(message)
    
INPUT_CHAIN_MESSAGE_PREFIX = "INPUT CHAIN"
PHOTO_MESSAGE_CHAINS: dict[str, PhotoMessageChain] = {} # circumvent 64-character limit of callback_data

class ImageMenu:
    def __init__(self, bot: TeleBot, worker):
        self.bot = bot
        self.worker = worker
        self.anti_flood = middlewares.get_anti_flood()
        self.menu_executor = DelayedExecutor(5)
        self.menu_callback_executor = DelayedExecutor(3)
        self.create_handlers()
        self.MAX_NUM_RETRIES = 2

    def image_menu(self, _, message: types.Message, parsed_data: dict):
        if message.content_type != "photo": return
        if message.chat.type == "private":
            signal = self.anti_flood.check(message.from_user.id, message)
            if type(signal) == middlewares.CancelUpdate: return
        print(f"Sending image menu to @{get_username(message.from_user)} ({message.from_user.id})")

        command_input_nodes = analyze_argument_from_preprocessed()
        id = str(message.id)
        pmc = PhotoMessageChain(id, self.bot, message, [])
        markup = types.InlineKeyboardMarkup()
        markup.row_width = 3
        markup.add(*[types.InlineKeyboardButton(command, callback_data=f"{command}|{id}") for command in command_input_nodes.keys()])
        markup.add(types.InlineKeyboardButton("close", callback_data=f"close|{id}"))
        reply_text = concat_strings(
            mention(message.from_user),
            f"IMAGE MENU (auto deleted after 30s) - Prompt: `{pmc.prompt}`",
        )
        reply_message = self.menu_executor(
            pmc.orig_message.chat,
            lambda: self.bot.reply_to(message, reply_text, reply_markup=markup, parse_mode="Markdown")
        )
        pmc.append(reply_message)
        def delete_message():
            try: self.bot.delete_message(reply_message.chat.id, reply_message.id)
            except: pass
            return schedule.CancelJob
        pmc.auto_close_job = schedule.every(30).seconds.do(delete_message)
        PHOTO_MESSAGE_CHAINS[id] = pmc

    def create_handlers(self):
        force_reply = types.ForceReply(selective=False)
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call: types.CallbackQuery):
            command, id = call.data.split('|')
            pmc = PHOTO_MESSAGE_CHAINS[id]
            if call.from_user.id != pmc.orig_message.from_user.id: return
            if command == "close":
                pmc.auto_close_job.run()
                return
            if pmc.orig_message.chat.type != "private":
                signal = self.anti_flood.check(call.from_user.id, call.message)
                if type(signal) == middlewares.CancelUpdate: return

            print(f"@{get_username(call.from_user)} ({call.from_user.id}) called {command}")
            command_input_nodes = analyze_argument_from_preprocessed()
            serialized_form = serialize_input_nodes(command, id, pmc.prompt, command_input_nodes[command].values())
            _, form, form_types = deserialize_input_chain_message(serialized_form)
            keys = list(form.keys())
            if len(keys) > 3:
                prompt = f"{form_types[keys[3]]} `{keys[3]}`?"
                reply_text = concat_strings(
                    INPUT_CHAIN_MESSAGE_PREFIX,
                    title_pad(),
                    serialized_form.replace('`', ''),
                    sep(),
                    prompt
                )
                pmc.append(self.bot.reply_to(pmc.orig_message, reply_text, reply_markup=force_reply, parse_mode="Markdown"))
            else:
                pmc.delete()
                pbar_message = self.menu_callback_executor(
                    pmc.orig_message.chat,
                    lambda: self.bot.reply_to(pmc.orig_message, "Executing...", parse_mode="Markdown")
                )
                pmc.append(pbar_message)
                self.worker.execute(
                    form["command"],
                    pmc.orig_message, form, 
                    pbar_message=pbar_message if pbar_message.chat.type == "private" else None, 
                    image_output_callback=lambda image_pil: self.finish(pmc, serialized_form, image_pil)
                )


        @self.bot.message_handler(func=lambda message: message.reply_to_message is not None, content_types=["text", "photo"])
        def input_chain(message: types.Message):
            orig_messsage = message.reply_to_message
            if not orig_messsage.text.startswith(INPUT_CHAIN_MESSAGE_PREFIX): return
            query, form, form_types = deserialize_input_chain_message(orig_messsage.text)
            pmc = PHOTO_MESSAGE_CHAINS[form["id"]]
            if message.from_user.id != pmc.orig_message.from_user.id: return

            if form_types[query] == "Photo":
                if message.content_type != "photo":
                    self.bot.send_message(message.chat.id, "The response is not photo. \nReup the first image and try again")
                    return pmc.delete()
                form[query] = "TG-" + max(message.photo, key=lambda p:p.width).file_id
            else:
                form[query] = message.caption if message.content_type == "photo" else message.text
            
            remain_keys = list(form.keys())
            remain_keys = remain_keys[remain_keys.index(query)+1:]
            serialized_form = '\n'.join([f"{form_types[k]} {k}: {v}" for k, v in form.items()])
            if (len(remain_keys)):
                prompt = f"{form_types[remain_keys[0]]} `{remain_keys[0]}`?"
                reply_text = concat_strings(
                    INPUT_CHAIN_MESSAGE_PREFIX,
                    title_pad(),
                    serialized_form.replace('`', ''),
                    sep(),
                    prompt
                )
                pmc.append(
                    self.bot.reply_to(pmc.orig_message, reply_text, reply_markup=force_reply, parse_mode="Markdown")
                )
                
            else:
                serialized_form = '\n'.join([
                    f"{form_types[k]} {k}: {v}" 
                    for k, v in form.items() if (k != "id" and form_types[k] != "Photo")
                ])
                reply_text = concat_strings(
                    "Form completed!",
                    title_pad(),
                    serialized_form.replace('`', ''),
                    sep(),
                    "Executing..."
                )
                pmc.delete()
                pbar_message = self.bot.reply_to(pmc.orig_message, reply_text, parse_mode="Markdown")
                pmc.append(pbar_message)
                self.worker.execute(
                    form["command"], 
                    pmc.orig_message, 
                    form,
                    pbar_message=pbar_message if pbar_message.chat.type == "private" else None,
                    image_output_callback=lambda image_pil: self.finish(pmc, serialized_form, image_pil)
                )
    
    def send_photo(self, orig_message, image_pil, num_retried=0):
        print(f"Sending output to @{get_username(orig_message.from_user)} ({orig_message.from_user.id})")
        caption = mention(orig_message.from_user)
        try:
            image_bytes = BytesIO()
            image_pil.save(image_bytes, format="PNG")
            image_bytes.seek(0)
            return self.bot.send_photo(
                orig_message.chat.id, 
                image_bytes, 
                caption='', 
                reply_to_message_id=orig_message.id, 
                parse_mode="Markdown"
            )
        except:
            pass
        try:
            image_bytes = BytesIO()
            image_pil.save(image_bytes, format="PNG")
            image_bytes.seek(0)
            return self.bot.send_photo(
                orig_message.chat.id, 
                image_bytes, 
                caption,
                parse_mode="Markdown"
            )
        except:
            num_retried += 1
            if num_retried > self.MAX_NUM_RETRIES:
                return self.bot.send_message(
                    orig_message.chat.id, 
                    f"{mention(orig_message.from_user)} Failed to send output image. Please retry again",
                    parse_mode="Markdown"
                )
            return self.send_photo(orig_message, image_pil, caption, num_retried)

    def finish(self, pmc: PhotoMessageChain, serialized_form, image_pil):
        finish_message = self.send_photo(pmc.orig_message, image_pil)
        pmc.delete()
        
        if SECRET_MONITOR_ROOM is not None:
            finish_text_full = concat_strings(
                mention(pmc.orig_message.from_user, True),
                title_pad(),
                serialized_form.replace('`', ''),
                sep(),
                "Input image"
            )
            input_image_message = self.bot.send_photo(
                SECRET_MONITOR_ROOM,
                min(pmc.orig_message.photo, key=lambda p:p.width).file_id,
                finish_text_full,
                parse_mode="Markdown"
            )
            self.bot.send_photo(
                input_image_message.chat.id,
                min(finish_message.photo, key=lambda p:p.width).file_id,
                "Output image",
                reply_to_message_id=input_image_message.id
            )