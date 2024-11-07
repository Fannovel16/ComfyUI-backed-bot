from telebot import types, TeleBot
from backed_bot_utils import get_username
from backed_bot_utils import get_username, get_dbm
import schedule, os
from datetime import datetime, timedelta
from dataclasses import dataclass

ADMIN_USER_ID = os.environ.get("ADMIN_USER_ID", '')

@dataclass
class AdvancedInfo:
    start_date: datetime
    duration_days: float

@dataclass
class UserInfo:
    id: str
    name: str
    is_allowed: bool = True
    advanced_info: AdvancedInfo = None

class AutoRevokeAdvanced:
    jobs: dict[str, schedule.Job] = {}
    @classmethod
    def create_job(cls, user_id: str, days: float):
        def job_func(user_id):
            with AuthManager.allowed_user_dbm() as allowed_users:
                allowed_users: dict[str, UserInfo]
                if user_id in allowed_users:
                    AuthManager.update_user_info(allowed_users, user_id, advanced_info=None)
                else:
                    print(f"Advanced user {user_id} is already revoked")
                del cls.jobs[user_id]
                return schedule.CancelJob
        
        cls.jobs[user_id] = schedule.every(days).days.do(job_func, user_id)
        return cls.jobs[user_id]
    
    @classmethod
    def cancel(cls, user_id):
        if user_id in cls.jobs:
            schedule.cancel_job(cls.jobs[user_id])

class AuthManager:
    @classmethod
    def allowed_user_dbm(cls):
        dbm = get_dbm("allowed_users")
        if not dbm:
            now = datetime.now()
            infinite_advanced_info = AdvancedInfo(now, (datetime.max - now).days)
            dbm[ADMIN_USER_ID] = UserInfo(ADMIN_USER_ID, "Admin", True, infinite_advanced_info)
        return dbm

    @classmethod
    def update_user_info(cls, allowed_users: dict[str, UserInfo], user_id, name=None, is_allowed=True, advanced_info: AdvancedInfo = None):
        user_info = allowed_users[user_id]
        user_info.name = name or user_info.name
        user_info.is_allowed = is_allowed
        user_info.advanced_info = advanced_info
        allowed_users[user_id] = user_info
    
    @classmethod
    def warmup(cls):
        # Avoid two dbm context as the same time
        with cls.allowed_user_dbm() as allowed_users:
            allowed_users: dict[str, UserInfo]
            advanced_infos = {user_id: allowed_users[user_id].advanced_info for user_id in allowed_users}
        for user_id, advance_info in advanced_infos.items():
            AutoRevokeAdvanced.cancel(user_id)
            AutoRevokeAdvanced.create_job(user_id, advance_info.duration_days)
    
    @classmethod
    def check_admin(cls, message, do_task):
        if str(message.from_user.id) != ADMIN_USER_ID:
            print(f"User {get_username(message.from_user)} ({message.from_user.id}) is not permited to {do_task}")
            return False
        return True
    
    @classmethod
    def serialize_allowed_users(cls, allowed_users: dict[str, UserInfo]):
        allowed = []
        for user_id, user_info in allowed_users.items():
            if not user_info.is_allowed: continue
            advanced_info = user_info.advanced_info
            if advanced_info is None:
                allowed.append(f"• _{user_info.name.replace('_', ' ')}_ (`{user_id}`): Normal")
            else:
                date_format = "%d/%m/%y %H:%M"
                start = advanced_info.start_date.strftime(date_format)
                end = (advanced_info.start_date + timedelta(days=advanced_info.duration_days)).strftime(date_format)
                allowed.append(f"• *{user_info.name} (*`{user_id}`*): Advanced*\n(`{start} – {end}`)")
        allowed_bullet_list = "\n".join(allowed)
        banned_bullet_list = "\n".join(
            f"• _{user_info.name.replace('_', ' ')}_  (`{user_id}`)"
            for user_id, user_info in allowed_users.items()
            if not user_info.is_allowed
        )
        text = f"Allowed users:\n{allowed_bullet_list}\nBanned users:\n{banned_bullet_list}"
        return text
    
    @classmethod
    def check_user_id(cls, user_id, allowed_users=None):
        if user_id == '*':
            return True
        try:
            if int(user_id) < 0: return False
        except: return False
        if user_id == ADMIN_USER_ID: return False
        if allowed_users is not None and user_id not in allowed_users:
            return False
        return True

    @classmethod
    def get_allowed(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "get allowed users"): return
        with cls.allowed_user_dbm() as allowed_users:
            allowed_users: dict[str, UserInfo]
            if allowed_users:
                bot.reply_to(message, cls.serialize_allowed_users(allowed_users), parse_mode="Markdown")
            else:
                bot.reply_to(message, "No user is allowed to use this bot yet")

    @classmethod
    def add_allowed(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "add allowed users"): return
        with cls.allowed_user_dbm() as allowed_users:
            allowed_users: dict[str, UserInfo]
            user_id_names = [
                str(s).split('/') if '/' in s \
                    else (s, "Everyone" if s.strip() == '*' else "Name_Unknown")
                for s in parsed_data["prompt"].split(',')
            ]
            for user_id, user_name in user_id_names:
                is_allowed = not user_id.startswith('-')
                user_id = user_id.strip() if is_allowed else user_id.strip()[1:]
                if not cls.check_user_id(user_id): continue
                user_name = user_name.replace('`', '').strip()
                # Avoid double dbm contexts
                AutoRevokeAdvanced.cancel(user_id)
                allowed_users[user_id] = UserInfo(id, user_name, is_allowed)
                
            bot.reply_to(message, cls.serialize_allowed_users(allowed_users), parse_mode="Markdown")

    @classmethod
    def remove_allowed(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "remove allowed users"): return
        text = "Removed successfully"
        with cls.allowed_user_dbm() as allowed_users:
            allowed_users: dict[str, UserInfo]
            user_ids = [s.strip() for s in parsed_data["prompt"].strip().split(',')]
            if "everyone" in user_ids:
                user_ids = allowed_users.keys()
            for user_id in user_ids:
                if not cls.check_user_id(user_id, allowed_users): continue
                # Avoid double dbm contexts
                AutoRevokeAdvanced.cancel(user_id)
                del allowed_users[user_id]
            text += '\n' + cls.serialize_allowed_users(allowed_users)
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
        with cls.allowed_user_dbm() as allowed_users:
            allowed_users: dict[str, UserInfo]
            for user_id, days in user_id_days:
                if not cls.check_user_id(user_id): continue
                if user_id not in allowed_users:
                    allowed_users[user_id] = UserInfo(user_id, "Unknown_Name", True)
                if not allowed_users[user_id].is_allowed:
                    text += f"User `{user_id[1:]}` is banned, therefore can't become an advanced user\n"
                    continue
                # Avoid double dbm contexts
                AutoRevokeAdvanced.cancel(user_id)
                cls.update_user_info(allowed_users, user_id, advanced_info=AdvancedInfo(datetime.now(), days))
                AutoRevokeAdvanced.create_job(user_id, days)
            text += cls.serialize_allowed_users(allowed_users)
            bot.reply_to(message, text, parse_mode="Markdown")

    @classmethod
    def remove_advanced(cls, bot: TeleBot, message: types.Message, parsed_data: dict):
        if not cls.check_admin(message, "remove advanced users"): return
        text = "Removed successfully\n"
        # auto_revoke_job has its own dbm context, thus running in cls.allowed_user_dbm() context will cause conflict
        # "pickle data was truncated"
        user_ids = [s.strip() for s in parsed_data["prompt"].strip().split(',')]
        with cls.allowed_user_dbm() as allowed_users:
            allowed_users: dict[str, UserInfo]
            for user_id in user_ids:
                # Avoid double dbm contexts
                AutoRevokeAdvanced.cancel(user_id)
                cls.update_user_info(allowed_users, user_id, advanced_info=None)
            text += cls.serialize_allowed_users(allowed_users)
        bot.reply_to(message, text, parse_mode="Markdown") 

AuthManager.warmup()
