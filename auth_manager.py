from telebot import types, TeleBot
from backed_bot_utils import get_username, get_dbm, get_sqldict_db
import schedule, os
from datetime import datetime, timedelta
from preprocess import analyze_argument_from_preprocessed
from dataclasses import dataclass, field

ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", '')
COMMAND_IS_ADVANCED = bool(int(os.environ.get("COMMAND_IS_ADVANCED", '1')))

@dataclass
class AdvancedInfo:
    start_date: datetime
    duration_days: float

class DefaultNormalUses:
    default_normal_uses = get_sqldict_db("default_normal_uses")

    @classmethod
    def warmup(cls):
        if not cls.default_normal_uses:
            cls.default_normal_uses["value"] = 5
    
    @classmethod
    def get(cls):
        return cls.default_normal_uses["value"]
    
    @classmethod
    def set(cls, new_default: int):
        cls.default_normal_uses["value"] = new_default

@dataclass
class UserInfo:
    id: str
    name: str
    is_allowed: bool = True
    remain_normal_uses: int = field(default_factory=DefaultNormalUses.get)
    advanced_info: AdvancedInfo = None

class AutoRevokeAdvanced:
    jobs: dict[str, schedule.Job] = {}
    @classmethod
    def create_job(cls, allowed_users: dict[str, UserInfo], user_id: str):
        def job_func(user_id):
            allowed_users: dict[str, UserInfo] = AuthManager.allowed_users
            if user_id in allowed_users:
                AuthManager.update_user_info(allowed_users, user_id, advanced_info=None)
            else:
                print(f"Advanced user {user_id} is already revoked")
            del cls.jobs[user_id]
            return schedule.CancelJob
        
        if user_id not in allowed_users or allowed_users[user_id].advanced_info is None:
            return cls.cancel(user_id)
        advanced_info = allowed_users[user_id].advanced_info
        revoke_date = advanced_info.start_date + timedelta(days=advanced_info.duration_days)
        remain_seconds = (revoke_date - datetime.now()).total_seconds()
        if remain_seconds < 0:
            AuthManager.update_user_info(allowed_users, user_id, advanced_info=None)
            return cls.cancel(user_id)
        cls.jobs[user_id] = schedule.every(remain_seconds).seconds.do(job_func, user_id)
        return cls.jobs[user_id]
    
    @classmethod
    def cancel(cls, user_id):
        if user_id in cls.jobs:
            schedule.cancel_job(cls.jobs[user_id])

class AuthManager:
    allowed_users = get_sqldict_db("allowed_users")

    @classmethod
    def update_user_info(cls, allowed_users: dict[str, UserInfo], user_id, **kwargs):
        user_info = allowed_users[user_id]
        if "name" in kwargs:
            user_info.name = kwargs["name"]
        if "is_allowed" in kwargs:
            user_info.is_allowed = kwargs["is_allowed"]
        if "advanced_info" in kwargs:
            user_info.advanced_info = kwargs["advanced_info"]
        if "remain_normal_uses" in kwargs:
            user_info.remain_normal_uses = kwargs["remain_normal_uses"]
        allowed_users[user_id] = user_info
    
    @classmethod
    def warmup(cls):
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        if not allowed_users:
            now = datetime.now()
            infinite_advanced_info = AdvancedInfo(now, (datetime.max - now).total_seconds()/3600/24)
            allowed_users[ADMIN_USER_ID] = UserInfo(ADMIN_USER_ID, "Admin", True, advanced_info=infinite_advanced_info)

        for user_id in allowed_users:
            AutoRevokeAdvanced.cancel(user_id)
            AutoRevokeAdvanced.create_job(allowed_users, user_id)
    
    @classmethod
    def check_admin(cls, message, do_task):
        if str(message.from_user.id) != ADMIN_USER_ID:
            print(f"User {get_username(message.from_user)} ({message.from_user.id}) is not permited to {do_task}")
            return False
        return True
    
    @classmethod
    def serialize_allowed_users(cls, display=["advanced", "banned"], filer_ids=None):
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        normal, advanced, banned = [], [], []
        normal: list[UserInfo]; advanced: list[UserInfo]; banned: list[UserInfo]
        for user_info in allowed_users.values():
            if filer_ids is not None:
                if user_info.id not in filer_ids: continue
            if not user_info.is_allowed:
                banned.append(user_info)
                continue
            if user_info.advanced_info is not None:
                advanced.append(user_info)
            else:
                normal.append(user_info)
        normal_str = "---------- Normal users ----------\n"
        normal_str += '\n'.join([
            f"• _{user_info.name.replace('_', ' ')}_ (`{user_info.id}`): Normal\n(`{user_info.remain_normal_uses}` free use(s) left)"
            for user_info in normal[:50]
        ])
        advanced = sorted(advanced, key=lambda user_info: user_info.advanced_info.duration_days, reverse=True)
        advanced_str = '---------- Advanced users ----------\n'
        for user_info in advanced:
            advanced_info = user_info.advanced_info
            date_format = "%d/%m/%y %H:%M"
            start = advanced_info.start_date.strftime(date_format)
            end = (advanced_info.start_date + timedelta(days=advanced_info.duration_days)).strftime(date_format)
            advanced_str += f"• *{user_info.name} (*`{user_info.id}`*): Advanced*\n(`{start} – {end}`)\n"
        advanced_str = advanced_str.strip()
        banned_str = "---------- Banned users ----------\n"
        banned_str += '\n'.join([
            f"• _{user_info.name.replace('_', ' ')}_  (`{user_info.id}`)"
            for user_info in banned
        ])
        output_str = ''
        if filer_ids is not None:
            if len(normal):
                output_str += normal_str + '\n\n'
            if len(advanced):
                output_str += advanced_str + '\n\n'
            if len(banned):
                output_str += banned_str + '\n\n'
            if len(output_str): return output_str.strip()
        for type in display:
            if type == "normal":
                output_str += normal_str + '\n\n'
            elif type == "advanced":
                output_str += advanced_str + '\n\n'
            else:
                output_str += banned_str + '\n\n'
        return output_str.strip()
    
    @classmethod
    def check_user_id(cls, user_id, allowed_users=None):
        if user_id == '*':
            return True
        try:
            if int(user_id) < 0: return False
        except: return False
        #if user_id == ADMIN_USER_ID: return False
        if allowed_users is not None and user_id not in allowed_users:
            return False
        return True

    @classmethod
    def get_allowed(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "get allowed users"): return
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        if not allowed_users:
            bot.reply_to(message, "No user is allowed to use this bot yet")
            return
        
        inputs = [inp.strip() for inp in parsed_data["prompt"].split(',') if len(inp.strip())]
        if "normal" in inputs or "advanced" in inputs or "banned" in inputs:
            bot.reply_to(message, cls.serialize_allowed_users(inputs), parse_mode="Markdown")
        elif len(inputs) == 0:
            bot.reply_to(message, cls.serialize_allowed_users(["advanced", "banned"]), parse_mode="Markdown")
        else:
            display = ["normal", "advanced", "banned"]
            bot.reply_to(message, cls.serialize_allowed_users(display, filer_ids=inputs), parse_mode="Markdown")

    @classmethod
    def add_allowed(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "add allowed users"): return
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        user_id_names = [
            str(s).split('/') if '/' in s \
                else (s, "Everyone" if s.strip() == '*' else "Name_Unknown")
            for s in parsed_data["prompt"].split(',')
        ]
        user_ids = []
        for user_id, user_name in user_id_names:
            is_allowed = not user_id.startswith('-')
            user_id = user_id.strip() if is_allowed else user_id.strip()[1:]
            if not cls.check_user_id(user_id): continue
            user_name = user_name.replace('`', '').strip()
            # Avoid double dbm contexts
            AutoRevokeAdvanced.cancel(user_id)
            allowed_users[user_id] = UserInfo(user_id, user_name, is_allowed)
            user_ids.append(user_id)
            
        bot.reply_to(message, cls.serialize_allowed_users(filer_ids=user_ids), parse_mode="Markdown")

    @classmethod
    def remove_allowed(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "remove allowed users"): return
        text = "Removed successfully"
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        user_ids = [s.strip() for s in parsed_data["prompt"].strip().split(',')]
        if "everyone" in user_ids:
            user_ids = allowed_users.keys()
        for user_id in user_ids:
            if not cls.check_user_id(user_id, allowed_users): continue
            # Avoid double dbm contexts
            AutoRevokeAdvanced.cancel(user_id)
            del allowed_users[user_id]
        text += '\n' + cls.serialize_allowed_users(filer_ids=user_ids)
        bot.reply_to(message, text, parse_mode="Markdown")

    @classmethod
    def add_advanced(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "add advanced users"): return
        user_id_days = [
            str(s).split('/') if '/' in s else (s, '1') 
            for s in parsed_data["prompt"].strip().split(',')
        ]
        user_id_days = [(id.strip(), float(day)) for id, day in user_id_days]
        text = ''
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        user_ids = []
        for user_id, days in user_id_days:
            if not cls.check_user_id(user_id): continue
            if user_id not in allowed_users:
                allowed_users[user_id] = UserInfo(user_id, "Unknown_Name", True)
            if not allowed_users[user_id].is_allowed:
                text += f"User `...{user_id[1:][-5:]}` is banned, therefore can't become an advanced user\n"
                continue
            AutoRevokeAdvanced.cancel(user_id)
            cls.update_user_info(allowed_users, user_id, advanced_info=AdvancedInfo(datetime.now(), days))
            AutoRevokeAdvanced.create_job(allowed_users, user_id)
            user_ids.append(user_id)
        text += cls.serialize_allowed_users(filer_ids=user_ids)
        bot.reply_to(message, text, parse_mode="Markdown")

    @classmethod
    def remove_advanced(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "remove advanced users"): return
        text = "Removed successfully\n"
        # auto_revoke_job has its own dbm context, thus running in cls.allowed_user_dbm() context will cause conflict
        # "pickle data was truncated"
        user_ids = [s.strip() for s in parsed_data["prompt"].strip().split(',')]
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        for user_id in user_ids:
            # Avoid double dbm contexts
            AutoRevokeAdvanced.cancel(user_id)
            cls.update_user_info(allowed_users, user_id, advanced_info=None)
        text += cls.serialize_allowed_users(filer_ids=user_ids)
        bot.reply_to(message, text, parse_mode="Markdown") 
    
    @classmethod
    def set_normal_uses(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "set normal uses"): return
        inputs = parsed_data["prompt"].strip().split(',')
        if '*' in parsed_data["prompt"]:
            user_id_use = inputs[0].split('/')
            if len(user_id_use) == 1:
                bot.reply_to(message, "Set normal uses for everyone requires explicit number")
                return
            _, uses = user_id_use
            DefaultNormalUses.set(uses)
            default_normal_uses = DefaultNormalUses.get()
            allowed_users: dict[str, UserInfo] = cls.allowed_users
            user_id_uses = [(id.strip(), default_normal_uses) for id in allowed_users]
        else:
            default_normal_uses = DefaultNormalUses.get()
            user_id_uses = [
                str(s).split('/') if '/' in s else (s, default_normal_uses) 
                for s in inputs
            ]
            user_id_uses = [(id.strip(), int(uses)) for id, uses in user_id_uses]
        allowed_users: dict[str, UserInfo] = cls.allowed_users
        user_ids = []
        for user_id, uses in user_id_uses:
            if not cls.check_user_id(user_id): continue
            cls.update_user_info(allowed_users, user_id, remain_normal_uses=uses)
            user_ids.append(user_id)
        bot.reply_to(message, cls.serialize_allowed_users(filer_ids=user_ids), parse_mode="Markdown") 

class ComfyCommandManager:
    command_manager = get_sqldict_db("command_manager")
    
    @classmethod
    def warmup(cls):
        available_cmds = list(analyze_argument_from_preprocessed().keys())
        commands: dict[str, bool] = cls.command_manager
        for existed_cmd in commands.keys():
            if existed_cmd not in available_cmds:
                del commands[existed_cmd]
        for available_cmd in available_cmds:
            if available_cmd not in commands:
                commands[available_cmd] = COMMAND_IS_ADVANCED

    @classmethod
    def serialize(cls, cmds):
        text = ''
        for cmd, is_advanced in cmds.items():
            text += f"• `{cmd}`: {'*Advanced*' if is_advanced else 'Normal'}" + '\n'
        return text
    
    @classmethod
    def get_commands(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not AuthManager.check_admin(message, "get commands"): return
        cmds: dict[str, bool] = cls.command_manager
        bot.reply_to(message, cls.serialize(cmds), parse_mode="Markdown")
    
    @classmethod
    def set_commands(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not AuthManager.check_admin(message, "set commands"): return
        input_cmds = [cmd.strip() for cmd in parsed_data["prompt"].split(',')]
        cmds: dict[str, bool] = cls.command_manager
        for input_cmd in input_cmds:
            is_advanced = not input_cmd.startswith('-')
            command = input_cmd.strip() if is_advanced else input_cmd.strip()[1:]
            if command not in cmds: continue
            cmds[command] = is_advanced
        bot.reply_to(message, cls.serialize(cmds), parse_mode="Markdown")

def warmup():
    DefaultNormalUses.warmup()
    AuthManager.warmup()
    ComfyCommandManager.warmup()
