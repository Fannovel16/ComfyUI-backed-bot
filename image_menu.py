from telebot import types, TeleBot
from preprocess import analyze_argument_from_preprocessed, serialize_input_nodes, deserialize_input_chain_message, CommandConfig
from dataclasses import dataclass
from backed_bot_utils import mention, get_username
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import os, schedule, time, middlewares
from auth_manager import AuthManager, UserInfo, ComfyCommandManager
from threading import Lock

SECRET_MONITOR_ROOM = os.environ.get("SECRET_MONITOR_ROOM", None)
IMAGE_FORMAT = os.environ.get("IMAGE_FORMAT", "png").upper()
TRIAL_IMAGE_FORMAT = os.environ.get("TRIAL_IMAGE_FORMAT", "jpeg").upper()

def concat_strings(*strs):
    return '\n'.join(strs)

def title_pad(title="SETTINGS", length=30, pad_char='-'):
    half_pad = (length - len(title) - 2) // 2
    return f"\n```\n{pad_char*half_pad} {title} {pad_char*half_pad}"

def sep(length=30, pad_char='-'):
    return pad_char*length + "\n```"

# Group rate limit is 20 messages/mintute
class DelayedExecutor:
    def __init__(self, normal_delay_secs=5, private_delay_secs=1):
        self.normal_executor = ThreadPoolExecutor(max_workers=2)
        self.private_executor = ThreadPoolExecutor(max_workers=2)
        self.normal_delay_secs = normal_delay_secs
        self.private_delay_secs = private_delay_secs

    def __call__(self, chat: types.Chat, func):
        def delayed_task(delay_secs):
            time.sleep(delay_secs)
            return func()

        if chat.type == "private":
            future = self.private_executor.submit(delayed_task, delay_secs=self.private_delay_secs)
        else:
            future = self.normal_executor.submit(delayed_task, delay_secs=self.normal_delay_secs)
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
Markup = types.InlineKeyboardMarkup
Button = types.InlineKeyboardButton

class ImageMenu:
    def __init__(self, bot: TeleBot, worker):
        self.bot = bot
        self.worker = worker
        self.anti_flood = middlewares.get_anti_flood()
        self.menu_executor = DelayedExecutor(2, 1)
        self.menu_callback_executor = DelayedExecutor(3, 0.5)
        self.create_handlers()
        self.MAX_NUM_RETRIES = 3
        self.finish_lock = Lock()
    
    def does_return_original(self, pmc, command):
        return pmc.orig_message.chat.type != "private" and command not in CommandConfig.get_no_return_original()

    def image_menu(self, _, message: types.Message, parsed_data: dict):
        if message.content_type != "photo": return
        if message.chat.type == "private":
            signal = self.anti_flood.check(message.from_user.id, message)
            if type(signal) == middlewares.CancelUpdate: return
        print(f"Sending image menu to @{get_username(message.from_user)} ({message.from_user.id})")

        cmd_display_names, _ = CommandConfig.get_display_names()
        guide_cmds = CommandConfig.get_guides()
        user_id = str(message.from_user.id)
        id = str(message.id)
        pmc = PhotoMessageChain(id, self.bot, message, [])
        
        allowed_users: dict[str, UserInfo] = AuthManager.allowed_users
        if user_id in allowed_users:
            is_user_advanced = allowed_users[user_id].advanced_info is not None
        else:
            is_user_advanced = False
        markup = Markup()
        cmds_advanced: dict[str, bool] = ComfyCommandManager.command_manager
        btns = [Button(
                (('' if is_user_advanced else '🔒') if cmds_advanced.get(cmd, False) else '') + display_name, 
                callback_data=f"{cmd}|{id}") 
            for cmd, display_name in cmd_display_names.items()]
        markup.add(*btns, row_width=2)
        if guide_cmds:
            markup.add(*[Button(
                cmd.display_name,
                callback_data=f"guide|{id}|{cmd.name}"
            ) for cmd in guide_cmds.values()], row_width=2)
        markup.add(
            Button(CommandConfig.CONFIG["display_names"].get("get_user_info", "Get User Info"), callback_data=f"get_user_info|{id}"),
            Button("close", callback_data=f"close|{id}"),
            row_width=1)
        reply_text = concat_strings(
            mention(message.from_user),
            f"IMAGE MENU (auto deleted after 30s) - Prompt: `{pmc.prompt}`",
        )
        reply_message = self.menu_executor(
            pmc.orig_message.chat,
            lambda: self.bot.reply_to(message, reply_text, reply_markup=markup, parse_mode="Markdown")
        )
        def delete_message(message):
            try: self.bot.delete_message(message.chat.id, message.id)
            except: pass
            finally: return schedule.CancelJob
        pmc.auto_close_job = schedule.every(30).seconds.do(delete_message, reply_message)
        PHOTO_MESSAGE_CHAINS[id] = pmc

    def free_global_pmc(self, orig_message_id):
        del PHOTO_MESSAGE_CHAINS[orig_message_id]
        return

    def create_handlers(self):
        force_reply = types.ForceReply(selective=False)
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call: types.CallbackQuery):
            command, id, *args = call.data.split('|')
            pmc = PHOTO_MESSAGE_CHAINS.get(id)
            user_id = str(pmc.orig_message.from_user.id)
            chat_id = pmc.orig_message.chat.id
            if pmc is None or (call.from_user.id != pmc.orig_message.from_user.id):
                return
            if command == "close":
                pmc.auto_close_job.run()
                return self.free_global_pmc(id)
            if command == "get_user_info":
                text = AuthManager.serialize_allowed_users(["normal", "advanced", "banned"], filer_ids=[user_id])
                self.bot.send_message(chat_id, text, parse_mode="Markdown")
                pmc.auto_close_job.run()
                return self.free_global_pmc(id)
            if command == "guide":
                guide = CommandConfig.get_guides()[args[0]]
                text_message = self.bot.send_message(chat_id, mention(pmc.orig_message.from_user) + '\n' + guide.text, parse_mode="Markdown")
                if guide.pil_images:
                    self.bot.send_media_group(
                        chat_id,
                        [types.InputMediaPhoto(pil_image) for pil_image in guide.pil_images],
                        reply_to_message_id=text_message.id
                    )
                pmc.auto_close_job.run()
                return self.free_global_pmc(id)
            if pmc.orig_message.chat.type != "private":
                signal = self.anti_flood.check(call.from_user.id, call.message)
                if type(signal) == middlewares.CancelUpdate: return self.free_global_pmc(id)
            if self.does_return_original(pmc, command):
                pmc.append(pmc.orig_message)

            allowed_users: dict[str, UserInfo] = AuthManager.allowed_users
            cmds_advanced: dict[str, bool] = ComfyCommandManager.command_manager
            user_info = allowed_users[user_id]
            if user_info.advanced_info is None:
                notify_message = None
                if cmds_advanced[command]:
                    reply_text = f"{mention(pmc.orig_message.from_user)} Acc free khong xai tinh nang nang cao duoc. Lien he thang admin de xin.\nFree account can't use advanced features. Contact admin."
                    notify_message = self.bot.send_message(pmc.orig_message.chat.id, reply_text, parse_mode="Markdown")
                elif user_info.remain_normal_uses <= 0:
                    reply_text = f"{mention(pmc.orig_message.from_user)} Het xai free duoc roi. Lien he thang admin de xin.\nNo free use left! Contact admin."
                    notify_message = self.bot.send_message(pmc.orig_message.chat.id, reply_text, parse_mode="Markdown")
                if notify_message is not None:
                    def auto_delete():
                        self.bot.delete_message(notify_message.chat.id, notify_message.id)
                        return schedule.CancelJob
                    schedule.every(10).seconds.do(auto_delete)
                    return
                remain_normal_uses = max(user_info.remain_normal_uses - 1, 0)
                AuthManager.update_user_info(user_id, remain_normal_uses=remain_normal_uses)

            print(f"@{get_username(call.from_user)} ({call.from_user.id}) called {command}")
            command_input_nodes = analyze_argument_from_preprocessed()
            serialized_form = serialize_input_nodes(command, id, pmc.prompt, command_input_nodes[command].values())
            _, form, form_types = deserialize_input_chain_message(serialized_form)
            keys = list(form.keys())
            mention_str = mention(pmc.orig_message.from_user)
            if len(keys) > 3:
                prompt = f"{form_types[keys[3]]} `{keys[3]}`?"
                reply_text = concat_strings(
                    INPUT_CHAIN_MESSAGE_PREFIX,
                    mention_str,
                    title_pad(),
                    serialized_form.replace('`', ''),
                    sep(),
                    prompt
                )
                pmc.append(self.bot.send_message(pmc.orig_message.chat.id, reply_text, reply_markup=force_reply, parse_mode="Markdown"))
            else:
                pmc.delete()
                pbar_message = self.menu_callback_executor(
                    pmc.orig_message.chat,
                    lambda: self.bot.send_message(pmc.orig_message.chat.id, f"{mention_str} Queuing...", parse_mode="Markdown")
                )
                pmc.append(pbar_message)
                self.free_global_pmc(pmc.id)
                self.worker.execute(
                    form["command"],
                    pmc.orig_message, form,
                    pbar_message=pbar_message,
                    image_output_callback=lambda image_pil: self.finish(form["command"], pmc, serialized_form, image_pil)
                )


        @self.bot.message_handler(func=lambda message: message.reply_to_message is not None and not (message.text or '').startswith("/get_ids"), content_types=["text", "photo"])
        def input_chain(message: types.Message):
            orig_messsage = message.reply_to_message
            text = orig_messsage.text or ''
            if not text.startswith(INPUT_CHAIN_MESSAGE_PREFIX): return
            query, form, form_types = deserialize_input_chain_message(orig_messsage.text)
            pmc = PHOTO_MESSAGE_CHAINS.get(form["id"])
            if pmc is None or (message.from_user.id != pmc.orig_message.from_user.id): return
            pmc.append(message)

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
                    mention(pmc.orig_message.from_user),
                    title_pad(),
                    serialized_form.replace('`', ''),
                    sep(),
                    prompt
                )
                pmc.append(
                    self.bot.send_message(pmc.orig_message.chat.id, reply_text, reply_markup=force_reply, parse_mode="Markdown")
                )
                
            else:
                serialized_form = '\n'.join([
                    f"{form_types[k]} {k}: {v}" 
                    for k, v in form.items() if (k != "id" and form_types[k] != "Photo")
                ])
                reply_text = concat_strings(
                    f"{mention(pmc.orig_message.from_user)} Form completed!",
                    title_pad(),
                    serialized_form.replace('`', ''),
                    sep(),
                    "Queuing..."
                )
                pmc.delete()
                pbar_message = self.bot.send_message(pmc.orig_message.chat.id, reply_text, parse_mode="Markdown")
                pmc.append(pbar_message)
                self.free_global_pmc(pmc.id)
                self.worker.execute(
                    form["command"], 
                    pmc.orig_message, form,
                    pbar_message=pbar_message,
                    image_output_callback=lambda image_pil: self.finish(form["command"], pmc, serialized_form, image_pil)
                )
    
    def send_photo(self, orig_message: types.Message, image_pil, image_format="PNG", return_original=True, num_retried=0):
        print(f"Sending output to @{get_username(orig_message.from_user)} ({orig_message.from_user.id})")
        mention_str = mention(orig_message.from_user)
        try:
            image_bytes = BytesIO()
            image_pil.save(image_bytes, format=IMAGE_FORMAT)
            image_bytes.seek(0)
            input_photo = types.InputMediaPhoto(max(orig_message.photo, key=lambda p:p.width).file_id)
            output_photo = types.InputMediaPhoto(image_bytes)
            output_photo = self.bot.send_media_group(
                orig_message.chat.id,
                [input_photo, output_photo] if return_original else [output_photo]
            )[-1].photo
            
            return (orig_message.photo, output_photo)
        
        except Exception as e:
            time.sleep(2)
            num_retried += 1
            if num_retried > self.MAX_NUM_RETRIES:
                self.bot.send_message(
                    orig_message.chat.id, 
                    f"{mention_str} Failed to send output image. Please retry again",
                    parse_mode="Markdown"
                )
                raise e
            return self.send_photo(orig_message, image_pil, image_format, return_original, num_retried)

    def finish(self, command: str, pmc: PhotoMessageChain, serialized_form, image_pil):
        with self.finish_lock:
            self.bot.send_message(
                pmc.orig_message.chat.id, 
                f"{mention(pmc.orig_message.from_user)} Images are showing up...", parse_mode="Markdown"
            )
            user_info: UserInfo = AuthManager.allowed_users[str(pmc.orig_message.from_user.id)]
            image_format = IMAGE_FORMAT if user_info.advanced_info else TRIAL_IMAGE_FORMAT
            try:
                photos_to_log = self.send_photo(
                    pmc.orig_message, image_pil,
                    image_format=image_format, return_original=self.does_return_original(pmc, command)
                )
            except Exception as e:
                raise e
            finally:
                pmc.delete()
        
        if SECRET_MONITOR_ROOM is not None:
            finish_text_full = concat_strings(
                mention(pmc.orig_message.from_user, True),
                title_pad(),
                serialized_form.replace('`', ''),
                sep()
            )
            message = self.bot.send_message(SECRET_MONITOR_ROOM, finish_text_full, parse_mode="Markdown")
            self.bot.send_media_group(
                SECRET_MONITOR_ROOM,
                [
                    types.InputMediaPhoto(min(photo, key=lambda p:p.width).file_id)
                    for photo in photos_to_log
                ],
                reply_to_message_id=message.id
            )