import asyncio
import os
import sys
import sqlite3

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_ID, BOT_TOKEN, HOURS_FOR_REVIEW, SECRET_PHRASE, FUNPAY_GOLDEN_KEY, PROXY_URL as CONF_PROXY_URL, PROXY_LOGIN as CONF_PROXY_LOGIN, PROXY_PASSWORD as CONF_PROXY_PASSWORD, MULTI_ACCOUNT_RENTAL_ENABLED
from databaseHandler.databaseSetup import SQLiteDB
from funpayHandler.funpay import send_message_by_owner
from logger import logger
from steamHandler.changePassword import changeSteamPassword

import requests

db_bot = SQLiteDB()
API_TOKEN = BOT_TOKEN

# --- ПРОКСИ НАСТРОЙКА ---
PROXY_URL = os.getenv("PROXY_URL") or CONF_PROXY_URL
PROXY_LOGIN = os.getenv("PROXY_LOGIN") or CONF_PROXY_LOGIN
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD") or CONF_PROXY_PASSWORD

def configure_proxy():
    import telebot.apihelper
    if PROXY_URL:
        telebot.apihelper.proxy = {
            "http": PROXY_URL,
            "https": PROXY_URL,
        }
    else:
        telebot.apihelper.proxy = None

configure_proxy()
# --- КОНЕЦ ПРОКСИ ---

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts")
try:
    os.makedirs(SAVE_DIR, exist_ok=True)
except PermissionError:
    SAVE_DIR = os.path.join(os.path.expanduser("~"), "UniFlex_accounts")
    os.makedirs(SAVE_DIR, exist_ok=True)

bot = telebot.TeleBot(API_TOKEN)
user_states = {}
whitelisted_users = set()

bot.set_my_commands(
    [
        telebot.types.BotCommand("/start", "Начать бота"),
        telebot.types.BotCommand("/accounts", "Посмотреть аккаунты"),
        telebot.types.BotCommand("/setproxy", "Установить прокси для бота"),
        telebot.types.BotCommand("/unsetproxy", "Сбросить прокси для бота"),
        telebot.types.BotCommand("/restart", "Перезапустить бота"),
        telebot.types.BotCommand("/unowned", "Свободные аккаунты"),
    ]
)

def set_user_state(user_id, state, data=None):
    user_states[user_id] = {"state": state, "data": data or {}}

def get_user_state(user_id):
    return user_states.get(user_id, {"state": None, "data": {}})

def clear_user_state(user_id):
    if user_id in user_states:
        del user_states[user_id]

# --- КРАСИВЫЕ КЛАВИАТУРЫ ---

def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📋 Мои аккаунты", callback_data="show_accounts"),
        InlineKeyboardButton("➕ Добавить аккаунты", callback_data="add_account"),
    )
    keyboard.add(
        InlineKeyboardButton("🔄 Сменить пароль", callback_data="change_password"),
        InlineKeyboardButton("⏹ Остановить аренду", callback_data="stop_rent"),
    )
    keyboard.add(
        InlineKeyboardButton("🤝 Ручная аренда", callback_data="manual_rent"),
        InlineKeyboardButton("⏰ Продлить время", callback_data="extend_rental"),
    )
    keyboard.add(
        InlineKeyboardButton("📊 Статистика", callback_data="statistics"),
        InlineKeyboardButton("🛠️ Настройки", callback_data="settings_menu"),
    )
    keyboard.add(
        InlineKeyboardButton("❓ Помощь", callback_data="help_menu"),
    )
    return keyboard

ACCOUNTS_PER_PAGE = 5

def get_accounts_pagination_keyboard(page, total_pages):
    keyboard = InlineKeyboardMarkup(row_width=2)
    if page > 0:
        keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data=f"accounts_page_{page - 1}"))
    if page < total_pages - 1:
        keyboard.add(InlineKeyboardButton("➡️ Вперёд", callback_data=f"accounts_page_{page + 1}"))
    keyboard.add(InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main"))
    return keyboard

@bot.callback_query_handler(func=lambda call: call.data == "show_accounts")
def show_accounts_callback(call):
    accounts = db_bot.get_all_accounts()
    if not accounts:
        bot.edit_message_text(
            "Аккаунты не найдены.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_main_keyboard()
        )
        return
    set_user_state(call.from_user.id, "viewing_accounts", {"accounts": accounts, "page": 0})
    send_accounts_page(call.message.chat.id, accounts, 0, call.message.message_id)

def send_accounts_page(chat_id, accounts, page, message_id=None):
    start = page * ACCOUNTS_PER_PAGE
    end = start + ACCOUNTS_PER_PAGE
    accounts_page = accounts[start:end]
    total_pages = (len(accounts) + ACCOUNTS_PER_PAGE - 1) // ACCOUNTS_PER_PAGE

    if not accounts_page:
        msg = "❗Нет больше аккаунтов для отображения."
    else:
        grouped_accounts = {}
        for account in accounts_page:
            account_name = account["account_name"]
            if account_name not in grouped_accounts:
                grouped_accounts[account_name] = []
            grouped_accounts[account_name].append(account)

        response = []
        for account_name, account_list in grouped_accounts.items():
            response.append(f"**📝 Название лота: `{account_name}`**")
            for account in account_list:
                account_id = account["id"]
                login = account["login"]
                password = account["password"]
                owner = account["owner"]
                order_id = account.get("order_id", "")
                account_info = (
                    f"🆔 ID: `{account_id}`\n"
                    f"🔑 Логин: `{login}`\n"
                    f"🔒 Пароль: `{password}`\n"
                )
                if owner:
                    account_info += f"👤 Владелец: `{owner}`"
                if order_id:
                    account_info += f"\n📦 Заказ: `{order_id}`"
                response.append(account_info)
        msg = "\n\n".join(response)

    keyboard = get_accounts_pagination_keyboard(page, total_pages)
    if message_id:
        bot.edit_message_text(
            msg,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        bot.send_message(
            chat_id,
            msg,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith("accounts_page_"))
def handle_accounts_pagination(call):
    page = int(call.data.split("_")[-1])
    state = get_user_state(call.from_user.id)
    if state["state"] == "viewing_accounts":
        accounts = state["data"]["accounts"]
        send_accounts_page(
            call.message.chat.id, accounts, page, message_id=call.message.message_id
        )
        set_user_state(
            call.from_user.id, "viewing_accounts", {"accounts": accounts, "page": page}
        )
    bot.answer_callback_query(call.id)


def get_settings_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔌 Прокси", callback_data="proxy_settings"),
        InlineKeyboardButton("👑 Голд кей", callback_data="gold_key_settings"),
    )
    keyboard.add(
        InlineKeyboardButton("⚙️ Система", callback_data="system_settings"),
        InlineKeyboardButton("📱 Уведомления", callback_data="notification_settings"),
    )
    keyboard.add(
        InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"),
    )
    return keyboard

def get_proxy_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔌 Установить/сменить", callback_data="proxy_set"),
        InlineKeyboardButton("❌ Сбросить", callback_data="proxy_unset"),
    )
    keyboard.add(
        InlineKeyboardButton("📊 Статус", callback_data="proxy_status"),
        InlineKeyboardButton("⬅️ Назад", callback_data="settings_menu"),
    )
    return keyboard

def get_gold_key_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Изменить", callback_data="gold_key_change"),
        InlineKeyboardButton("🔎 Проверить", callback_data="gold_key_check"),
    )
    keyboard.add(
        InlineKeyboardButton("⬅️ Назад", callback_data="settings_menu"),
    )
    return keyboard

def get_system_settings_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔄 Автообновление", callback_data="auto_refresh_toggle"),
        InlineKeyboardButton("⏰ Таймауты", callback_data="timeout_settings"),
    )
    keyboard.add(
        InlineKeyboardButton("🔢 Множественная выдача", callback_data="multi_account_settings"),
        InlineKeyboardButton("🗄️ База данных", callback_data="database_settings"),
    )
    keyboard.add(
        InlineKeyboardButton("⬅️ Назад", callback_data="settings_menu"),
    )
    return keyboard

def get_notification_settings_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔔 Новые заказы", callback_data="notify_new_orders"),
        InlineKeyboardButton("⏰ Истечение аренды", callback_data="notify_expiry"),
    )
    keyboard.add(
        InlineKeyboardButton("❌ Ошибки", callback_data="notify_errors"),
        InlineKeyboardButton("⬅️ Назад", callback_data="settings_menu"),
    )
    return keyboard

def get_multi_account_settings_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ Включить", callback_data="multi_account_enable"),
        InlineKeyboardButton("❌ Выключить", callback_data="multi_account_disable"),
    )
    keyboard.add(
        InlineKeyboardButton("📊 Статус", callback_data="multi_account_status"),
        InlineKeyboardButton("⬅️ Назад", callback_data="system_settings"),
    )
    return keyboard

def get_accounts_pagination_keyboard(page, total_pages):
    keyboard = InlineKeyboardMarkup(row_width=2)
    if page > 0:
        keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data=f"accounts_page_{page - 1}"))
    if page < total_pages - 1:
        keyboard.add(InlineKeyboardButton("➡️ Вперёд", callback_data=f"accounts_page_{page + 1}"))
    keyboard.add(InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_main"))
    return keyboard

# --- МЕНЮ НАСТРОЕК ---
@bot.callback_query_handler(func=lambda call: call.data == "settings_menu")
def settings_menu_callback(call):
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🛠️ <b>Настройки</b>",
        reply_markup=get_settings_keyboard(),
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)

# --- ГОЛД КЕЙ НАСТРОЙКИ ---
@bot.callback_query_handler(func=lambda call: call.data == "gold_key_settings")
def gold_key_settings_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    keyboard = get_gold_key_keyboard()
    current_key = get_gold_key_from_config()
    display_key = current_key if current_key else "Не задан"
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"👑 <b>Голд кей</b>\n\nТекущий Голд кей: <code>{display_key}</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "gold_key_change")
def gold_key_change_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    set_user_state(call.from_user.id, "waiting_for_gold_key")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="✏️ Введите новый Голд кей:",
        reply_markup=get_gold_key_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "gold_key_check")
def gold_key_check_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    key = get_gold_key_from_config()
    check_result, error_msg = check_funpay_golden_key(key)
    if check_result:
        bot.answer_callback_query(call.id, "Голд кей валидный ✅", show_alert=True)
    else:
        bot.answer_callback_query(call.id, f"Голд кей невалидный ❌\n{error_msg}", show_alert=True)

@bot.message_handler(func=lambda message: get_user_state(message.from_user.id)["state"] == "waiting_for_gold_key")
def process_gold_key(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "Доступ запрещён.")
        return
    new_key = message.text.strip()
    res = update_gold_key_in_config(new_key)
    if res:
        bot.send_message(message.chat.id, f"🤑Голд кей успешно изменён!\nНовый ключ: <code>{new_key}</code>", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, "❌Ошибка при сохранении ключа в config.py. Проверьте права доступа.")
    clear_user_state(message.from_user.id)

def get_gold_key_from_config():
    try:
        import importlib.util
        import sys
        config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.py'))
        spec = importlib.util.spec_from_file_location("config", config_path)
        config = importlib.util.module_from_spec(spec)
        sys.modules["config"] = config
        spec.loader.exec_module(config)
        return getattr(config, "FUNPAY_GOLDEN_KEY", "")
    except Exception as e:
        print(f"[get_gold_key_from_config] {e}")
        return ""

def update_gold_key_in_config(new_key):
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.py'))
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        for idx, line in enumerate(lines):
            if line.strip().startswith("FUNPAY_GOLDEN_KEY"):
                lines[idx] = f'FUNPAY_GOLDEN_KEY = "{new_key}"\n'
                found = True
                break
        if not found:
            lines.append(f'\nFUNPAY_GOLDEN_KEY = "{new_key}"\n')
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        print(f"Ошибка при записи FUNPAY_GOLDEN_KEY: {e}")
        return False

def get_multi_account_setting():
    """Get current multi-account rental setting from config"""
    try:
        import importlib.util
        import sys
        config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.py'))
        spec = importlib.util.spec_from_file_location("config", config_path)
        config = importlib.util.module_from_spec(spec)
        sys.modules["config"] = config
        spec.loader.exec_module(config)
        return getattr(config, "MULTI_ACCOUNT_RENTAL_ENABLED", True)
    except Exception as e:
        print(f"[get_multi_account_setting] {e}")
        return True

def update_multi_account_setting(enabled):
    """Update multi-account rental setting in config"""
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.py'))
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        for idx, line in enumerate(lines):
            if line.strip().startswith("MULTI_ACCOUNT_RENTAL_ENABLED"):
                lines[idx] = f'MULTI_ACCOUNT_RENTAL_ENABLED = {enabled}\n'
                found = True
                break
        if not found:
            lines.append(f'\nMULTI_ACCOUNT_RENTAL_ENABLED = {enabled}\n')
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        print(f"Ошибка при записи MULTI_ACCOUNT_RENTAL_ENABLED: {e}")
        return False

def check_funpay_golden_key(key):
    try:
        headers = {
            "cookie": f"golden_key={key}",
            "user-agent": "Mozilla/5.0"
        }
        resp = requests.get("https://funpay.com/", headers=headers, timeout=7)
        if resp.status_code == 200:
            if "Профиль" in resp.text or "profile" in resp.text.lower():
                return True, ""
            if "Войти" in resp.text or "login" in resp.text.lower():
                return False, "Ключ не авторизован (вы не вошли в профиль)"
            return False, "Не удалось однозначно определить валидность ключа"
        else:
            return False, f"Сайт ответил с кодом {resp.status_code}"
    except Exception as e:
        return False, f"Ошибка проверки: {e}"

# --- ПРОКСИ СОХРАНЕНИЕ В CONFIG.PY ---
def update_proxy_in_config(proxy_url, proxy_login, proxy_password):
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.py'))
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        params = {
            "PROXY_URL": proxy_url,
            "PROXY_LOGIN": proxy_login,
            "PROXY_PASSWORD": proxy_password
        }
        for key, value in params.items():
            found = False
            for idx, line in enumerate(lines):
                if line.strip().startswith(f"{key}"):
                    lines[idx] = f'{key} = "{value}"\n'
                    found = True
                    break
            if not found:
                lines.append(f'\n{key} = "{value}"\n')
        with open(config_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except Exception as e:
        print(f"Ошибка при записи прокси: {e}")
        return False
# --- КОНЕЦ ПРОКСИ СОХРАНЕНИЯ ---

# --- ПРОКСИ КНОПКИ ---
@bot.callback_query_handler(func=lambda call: call.data == "proxy_settings")
def proxy_settings_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    keyboard = get_proxy_keyboard()
    current_proxy = PROXY_URL if PROXY_URL else "Не задан"
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🛡️ <b>Прокси</b>\n\nПрокси сейчас: <code>{current_proxy}</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "proxy_set")
def proxy_set_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    set_user_state(call.from_user.id, "waiting_for_proxy_url")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🔌 <b>Установка прокси</b>\n\nОтправьте прокси в формате:\n<code>http(s)://[login:password@]host:port</code>",
        parse_mode="HTML",
        reply_markup=get_proxy_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "proxy_unset")
def proxy_unset_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    import telebot.apihelper
    telebot.apihelper.proxy = None
    os.environ.pop("PROXY_URL", None)
    os.environ.pop("PROXY_LOGIN", None)
    os.environ.pop("PROXY_PASSWORD", None)
    global PROXY_URL, PROXY_LOGIN, PROXY_PASSWORD
    PROXY_URL = ""
    PROXY_LOGIN = ""
    PROXY_PASSWORD = ""
    update_proxy_in_config("", "", "")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="❌ Прокси сброшен! Рекомендуется перезапустить бота.",
        reply_markup=get_proxy_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "proxy_check")
def proxy_check_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён.", show_alert=True)
        return
    proxy_url = PROXY_URL
    if not proxy_url:
        bot.answer_callback_query(call.id, "Прокси не задан.", show_alert=True)
        return
    if "://" not in proxy_url:
        bot.answer_callback_query(call.id, "Прокси некорректный.", show_alert=True)
        return
    proxies = { "http": proxy_url, "https": proxy_url }
    try:
        r = requests.get("https://api.telegram.org", proxies=proxies, timeout=7)
        if r.status_code == 200:
            bot.answer_callback_query(call.id, "Прокси рабочий ✅", show_alert=True)
        else:
            bot.answer_callback_query(call.id, f"Ошибка прокси: {r.status_code}", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Прокси не работает: {e}", show_alert=True)

# --- ПРОКСИ КОМАНДЫ ---
@bot.message_handler(commands=["setproxy"])
def set_proxy_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "Доступ запрещён.")
        return
    set_user_state(message.from_user.id, "waiting_for_proxy_url")
    bot.send_message(message.chat.id, "🔌 <b>Установка прокси</b>\n\nОтправьте прокси в формате:\n<code>http(s)://[login:password@]host:port</code>", parse_mode="HTML")

@bot.message_handler(commands=["unsetproxy"])
def unset_proxy_command(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "Доступ запрещён.")
        return
    import telebot.apihelper
    telebot.apihelper.proxy = None
    os.environ.pop("PROXY_URL", None)
    os.environ.pop("PROXY_LOGIN", None)
    os.environ.pop("PROXY_PASSWORD", None)
    global PROXY_URL, PROXY_LOGIN, PROXY_PASSWORD
    PROXY_URL = ""
    PROXY_LOGIN = ""
    PROXY_PASSWORD = ""
    update_proxy_in_config("", "", "")
    bot.send_message(message.chat.id, "❌ Прокси сброшен! Рекомендуется перезапустить бота.")

@bot.message_handler(func=lambda message: get_user_state(message.from_user.id)["state"] == "waiting_for_proxy_url")
def process_proxy_url(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "Доступ запрещён.")
        return
    import telebot.apihelper
    url = message.text.strip()
    try:
        if "://" not in url:
            bot.send_message(message.chat.id, "Ошибка: укажите протокол (http:// или https://) в начале строки прокси!")
            return
        os.environ["PROXY_URL"] = url
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            auth, endpoint = rest.split("@", 1)
            if ":" in auth:
                login, password = auth.split(":", 1)
                os.environ["PROXY_LOGIN"] = login
                os.environ["PROXY_PASSWORD"] = password
            else:
                os.environ["PROXY_LOGIN"] = auth
                os.environ["PROXY_PASSWORD"] = ""
            proxy_url_auth = f"{scheme}://{auth}@{endpoint}"
        else:
            os.environ["PROXY_LOGIN"] = ""
            os.environ["PROXY_PASSWORD"] = ""
            proxy_url_auth = url
        telebot.apihelper.proxy = {
            "http": proxy_url_auth,
            "https": proxy_url_auth,
        }
        global PROXY_URL, PROXY_LOGIN, PROXY_PASSWORD
        PROXY_URL = url
        PROXY_LOGIN = os.environ.get("PROXY_LOGIN")
        PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD")
        update_proxy_in_config(PROXY_URL, PROXY_LOGIN, PROXY_PASSWORD)
        proxies = {"http": proxy_url_auth, "https": proxy_url_auth}
        try:
            r = requests.get("https://api.telegram.org", proxies=proxies, timeout=7)
            if r.status_code == 200:
                bot.send_message(message.chat.id, f"Прокси установлен и рабочий ✅\n{proxy_url_auth}\nРекомендуется перезапустить бота для применения прокси во всех потоках.")
            else:
                bot.send_message(message.chat.id, f"Прокси установлен (но не рабочий, код {r.status_code}): {proxy_url_auth}")
        except Exception as e:
            bot.send_message(message.chat.id, f"Прокси установлен, но не рабочий: {e}")
        clear_user_state(message.from_user.id)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка установки прокси: {e}")

# --- КОНЕЦ ПРОКСИ ---

@bot.callback_query_handler(func=lambda call: call.data == "statistics")
def statistics_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    try:
        stats = db_bot.get_rental_statistics()
        
        if stats:
            message = (
                "📊 **Статистика системы аренды:**\n\n"
                f"🔢 **Всего аккаунтов:** `{stats['total_accounts']}`\n"
                f"✅ **Активных аренд:** `{stats['active_rentals']}`\n"
                f"🆓 **Свободных аккаунтов:** `{stats['available_accounts']}`\n"
                f"⏰ **Общее время аренды:** `{stats['total_hours']}` часов\n"
                f"🆕 **Новых аренд (24ч):** `{stats['recent_rentals']}`\n\n"
                f"📈 **Загруженность:** `{(stats['active_rentals'] / stats['total_accounts'] * 100):.1f}%`"
            )
        else:
            message = "❌ Не удалось получить статистику"
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔄 Обновить", callback_data="statistics"))
        keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
        
        bot.edit_message_text(
            message,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)}")
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "help_menu")
def help_menu_callback(call):
    help_text = (
        "❓ **Справка по использованию бота:**\n\n"
        "📋 **Мои аккаунты** - просмотр всех ваших арендованных аккаунтов\n"
        "➕ **Добавить аккаунты** - добавление новых аккаунтов в систему\n"
        "🔄 **Сменить пароль** - смена пароля для конкретного аккаунта\n"
        "⏹ **Остановить аренду** - досрочное прекращение аренды\n"
        "🤝 **Ручная аренда** - ручное назначение аккаунта пользователю\n"
        "⏰ **Продлить время** - продление срока аренды\n"
        "📊 **Статистика** - общая статистика системы\n"
        "🛠️ **Настройки** - настройка прокси и других параметров\n\n"
        "💡 **Полезные команды:**\n"
        "/start - главное меню\n"
        "/accounts - список всех аккаунтов\n"
        "/setproxy - установить прокси\n"
        "/unsetproxy - сбросить прокси\n"
        "/restart - перезапустить бота\n\n"
        "🔐 **Система продления:**\n"
        "• Автоматическое продление на 1 час при оставлении отзыва\n"
        "• Ручное продление через меню 'Продлить время'\n"
        "• Автоматическая смена пароля при истечении срока\n"
        "• Предупреждение за 10 минут до истечения аренды"
    )
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main"))
    
    bot.edit_message_text(
        help_text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=keyboard
    )
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    bot.edit_message_text(
        "🎮 **Steam Rental by Kylichonok**\n\n"
        "Выберите нужную функцию:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=["start"])
def start(message):
    if message.from_user.id not in whitelisted_users:
        set_user_state(message.from_user.id, "waiting_for_secret_phrase", {})
        bot.send_message(
            message.chat.id,
            "🔐 **Добро пожаловать в Steam Rental by Kylichonok!**\n\n"
            "Для доступа к системе введите секретную фразу:",
            parse_mode="Markdown"
        )
        return

    # Получаем статистику для приветствия
    try:
        stats = db_bot.get_rental_statistics()
        welcome_stats = ""
        if stats:
            welcome_stats = (
                f"\n📊 **Статистика системы:**\n"
                f"• Активных аренд: `{stats['active_rentals']}`\n"
                f"• Свободных аккаунтов: `{stats['available_accounts']}`\n"
                f"• Загруженность: `{(stats['active_rentals'] / stats['total_accounts'] * 100):.1f}%`"
            )
    except:
        welcome_stats = ""

    welcome_message = (
        "🎮 **Добро пожаловать в Steam Rental by Kylichonok!**\n\n"
        "🚀 **Система автоматической аренды Steam аккаунтов**\n\n"
        "✨ **Возможности:**\n"
        "• Автоматическая обработка заказов с FunPay\n"
        "• Умная система продления аренды\n"
        "• Автоматическая смена паролей\n"
        "• Telegram бот для управления\n"
        "• Статистика и аналитика\n\n"
        "🔐 **Система продления:**\n"
        "• Автоматическое продление на 1 час при отзыве\n"
        "• Ручное продление через бот\n"
        "• Уведомления об истечении срока\n\n"
        "Выберите нужную функцию:" + welcome_stats
    )

    bot.send_message(
        message.chat.id,
        welcome_message,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"] == "waiting_for_secret_phrase"
)
def process_secret_phrase(message):
    if message.text == SECRET_PHRASE:
        whitelisted_users.add(message.from_user.id)
        clear_user_state(message.from_user.id)
        all_accounts = len(db_bot.get_all_accounts())
        owned_accounts = all_accounts - len(db_bot.get_unowned_accounts())
        bot.send_message(
            message.chat.id,
            f"Добро пожаловать!\nВот статистика на данный момент: {owned_accounts}/{all_accounts}",
            reply_markup=get_main_keyboard(),
        )
    else:
        bot.send_message(message.chat.id, "Неверная фраза. Попробуйте снова.")

@bot.callback_query_handler(func=lambda call: call.data == "add_account")
def process_add_account(call):
    set_user_state(call.from_user.id, "waiting_for_lot_count", {})
    bot.send_message(call.message.chat.id, "Сколько лотов вы хотите добавить?")
    bot.answer_callback_query(call.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_lot_count"
)
def process_lot_count(message):
    if not message.text.isdigit() or int(message.text) <= 0:
        bot.send_message(message.chat.id, "Пожалуйста, введите положительное число.")
        return

    lot_count = int(message.text)
    set_user_state(
        message.from_user.id,
        "waiting_for_lot_names",
        {"lot_count": lot_count, "current_lot": 0, "lot_names": []},
    )
    bot.send_message(message.chat.id, "Введите название для лота 1.")

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_lot_names"
)
def process_lot_names(message):
    state_data = get_user_state(message.from_user.id)["data"]
    state_data["lot_names"].append(message.text)
    state_data["current_lot"] += 1

    if state_data["current_lot"] < state_data["lot_count"]:
        set_user_state(message.from_user.id, "waiting_for_lot_names", state_data)
        bot.send_message(
            message.chat.id,
            f"Введите название для лота {state_data['current_lot'] + 1}.",
        )
    else:
        set_user_state(
            message.from_user.id,
            "waiting_for_count",
            {"lot_names": state_data["lot_names"]},
        )
        bot.send_message(
            message.chat.id, "Сколько аккаунтов вы хотите добавить для каждого лота?"
        )

@bot.callback_query_handler(func=lambda call: call.data == "delete_account")
def process_delete_account(call):
    set_user_state(call.from_user.id, "waiting_for_account_id", {})
    bot.send_message(
        call.message.chat.id, "Введите ID аккаунта, который вы хотите удалить."
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "change_password")
def process_change_password(call):
    set_user_state(call.from_user.id, "waiting_for_change_password_id", {})
    bot.send_message(
        call.message.chat.id,
        "Введите ID аккаунта, для которого вы хотите сменить пароль.",
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "stop_rent")
def process_stop_rent(call):
    set_user_state(call.from_user.id, "waiting_for_stop_rent_id", {})
    bot.send_message(
        call.message.chat.id,
        "Введите ID аккаунта, аренду которого вы хотите остановить.",
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "manual_rent")
def manual_rent_callback(call):
    set_user_state(call.from_user.id, "waiting_for_manual_rent_id", {})
    bot.send_message(
        call.message.chat.id, "Введите ID аккаунта, который вы хотите арендовать."
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "extend_rental")
def extend_rental_callback(call):
    set_user_state(call.from_user.id, "waiting_for_extend_rental_id", {})
    bot.send_message(
        call.message.chat.id, "Введите ID аккаунта, аренду которого вы хотите продлить."
    )
    bot.answer_callback_query(call.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_owner_name"
)
def process_owner_name(message):
    owner_name = message.text
    state_data = {"owner_name": owner_name}
    set_user_state(message.from_user.id, "waiting_for_hours_to_add", state_data)
    bot.send_message(
        message.chat.id,
        f"Введите количество часов, которые вы хотите добавить для {owner_name}.",
    )

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_hours_to_add"
)
def process_hours_to_add(message):
    if not message.text.isdigit() or int(message.text) <= 0:
        bot.send_message(
            message.chat.id, "Пожалуйста, введите положительное число часов."
        )
        return

    hours_to_add = int(message.text)
    state_data = get_user_state(message.from_user.id)["data"]
    owner_name = state_data["owner_name"]

    try:
        if db_bot.add_time_to_owner_accounts(
            owner_name, -hours_to_add
        ):
            bot.send_message(
                message.chat.id,
                f"Успешно добавлено {hours_to_add} часов для всех аккаунтов владельца '{owner_name}'.",
            )

            send_message_by_owner(
                owner=owner_name,
                message=(
                    f"Вам добавлено {hours_to_add} часов аренды.\n\n"
                    f"Если вы хотите продлить аренду, напишите администратору."
                ),
            )
        else:
            bot.send_message(
                message.chat.id,
                f"Не удалось найти аккаунты для владельца '{owner_name}' или добавить часы.",
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при добавлении часов: {str(e)}")
    finally:
        clear_user_state(message.from_user.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_count"
)
def process_count(message):
    if not message.text.isdigit() or int(message.text) <= 0:
        bot.send_message(message.chat.id, "Пожалуйста, введите положительное число.")
        return

    count = int(message.text)
    state_data = get_user_state(message.from_user.id)["data"]
    state_data.update({"total_count": count, "current_lot": 0, "lot_durations": {}, "current_account": 0})
    set_user_state(message.from_user.id, "waiting_for_lot_duration", state_data)
    bot.send_message(
        message.chat.id,
        f"На сколько часов будет сдаваться лот \n```{state_data['lot_names'][0]}```",
        parse_mode="Markdown",
    )

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_lot_duration"
)
def process_lot_duration(message):
    if not message.text.isdigit() or int(message.text) <= 0:
        bot.send_message(
            message.chat.id, "Пожалуйста, введите положительное число часов."
        )
        return

    state_data = get_user_state(message.from_user.id)["data"]
    current_lot = state_data["current_lot"]
    lot_name = state_data["lot_names"][current_lot]
    state_data["lot_durations"][lot_name] = int(message.text)

    if current_lot + 1 < len(state_data["lot_names"]):
        state_data["current_lot"] += 1
        set_user_state(message.from_user.id, "waiting_for_lot_duration", state_data)
        bot.send_message(
            message.chat.id,
            f"На сколько часов будет сдаваться лот \n```{state_data['lot_names'][current_lot + 1]}```",
            parse_mode="Markdown",
        )
    else:
        state_data["current_count"] = 0
        state_data["current_account"] = 0
        set_user_state(message.from_user.id, "waiting_for_mafile", state_data)
        bot.send_message(
            message.chat.id, f"Пожалуйста, загрузите .maFile для аккаунта {state_data['current_account'] + 1}."
        )

@bot.message_handler(content_types=["document"])
def process_mafile(message):
    state = get_user_state(message.from_user.id)
    if state["state"] != "waiting_for_mafile":
        return

    if not message.document.file_name.endswith(".maFile"):
        bot.send_message(
            message.chat.id, "Пожалуйста, загрузите валидный .maFile файл."
        )
        return

    state_data = state["data"]
    current_account = state_data.get("current_account", 0)

    try:
        file_name = message.document.file_name
        file_path = os.path.join(SAVE_DIR, file_name)

        if os.path.exists(file_path):
            os.remove(file_path)

        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(file_path, "wb") as f:
            f.write(downloaded_file)

        relative_path = os.path.relpath(file_path, start=os.getcwd())
        state_data["mafile_path"] = relative_path

        set_user_state(message.from_user.id, "waiting_for_login", state_data)
        bot.send_message(
            message.chat.id, f"Ваш .maFile сохранен для аккаунта {current_account + 1}. Теперь отправьте логин."
        )
        logger.info(f"Обработка .maFile для аккаунта {current_account + 1}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при сохранении файла: {str(e)}")
        logger.error(f"Ошибка при сохранении .maFile для аккаунта {current_account + 1}: {e}")

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_login"
)
def process_login(message):
    state_data = get_user_state(message.from_user.id)["data"]
    state_data["login"] = message.text
    current_account = state_data.get("current_account", 0)
    set_user_state(message.from_user.id, "waiting_for_password", state_data)
    bot.send_message(message.chat.id, f"Логин сохранен для аккаунта {current_account + 1}. Теперь отправьте пароль.")

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_password"
)
def process_password(message):
    state_data = get_user_state(message.from_user.id)["data"]
    current_account = state_data.get("current_account", 0)

    # Создаем один аккаунт для всех лотов с текущим логином и паролем
    success_count = 0
    failed_lots = []
    
    for lot_name in state_data["lot_names"]:
        try:
            if db_bot.add_account(
                account_name=lot_name,
                path_to_maFile=state_data["mafile_path"],
                login=state_data["login"],
                password=message.text,
                duration=state_data["lot_durations"][lot_name],
            ):
                success_count += 1
                logger.info(f"Успешно добавлен лот '{lot_name}' для аккаунта {current_account + 1}")
            else:
                failed_lots.append(lot_name)
                logger.error(f"Не удалось добавить лот '{lot_name}' для аккаунта {current_account + 1}")
        except Exception as e:
            failed_lots.append(lot_name)
            logger.error(f"Ошибка при добавлении лота '{lot_name}': {e}")

    if success_count > 0:
        message_text = f"✅ Успешно добавлено {success_count} из {len(state_data['lot_names'])} лотов для аккаунта {current_account + 1}."
        if failed_lots:
            message_text += f"\n❌ Не удалось добавить лоты: {', '.join(failed_lots)}"
        bot.send_message(message.chat.id, message_text)
    else:
        bot.send_message(
            message.chat.id,
            f"❌ Ошибка при добавлении аккаунта {current_account + 1}. Попробуйте снова."
        )
        return

    current_account += 1
    if current_account < state_data["total_count"]:
        state_data["current_account"] = current_account
        set_user_state(message.from_user.id, "waiting_for_mafile", state_data)
        bot.send_message(
            message.chat.id,
            f"Пожалуйста, загрузите .maFile для аккаунта {current_account + 1}.",
        )
    else:
        clear_user_state(message.from_user.id)
        bot.send_message(
            message.chat.id,
            f"🎉 Все {state_data['total_count']} аккаунтов успешно добавлены! Настройка завершена.",
        )

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_account_id"
)
def delete_account_by_id_handler(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Пожалуйста, введите валидный числовой ID.")
        return

    account_id = int(message.text)
    if db_bot.delete_account_by_id(account_id):
        bot.send_message(message.chat.id, f"Аккаунт с ID {account_id} успешно удален.")
    else:
        bot.send_message(
            message.chat.id, f"Не удалось найти или удалить аккаунт с ID {account_id}."
        )

    clear_user_state(message.from_user.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_change_password_id"
)
def change_password_by_id_handler(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Пожалуйста, введите валидный числовой ID.")
        return

    account_id = int(message.text)
    bot.send_message(
        message.chat.id, f"🔐 Изменение пароля для аккаунта с ID {account_id}..."
    )
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT path_to_maFile, password
            FROM accounts
            WHERE ID = ?
            """,
            (account_id,),
        )
        account = cursor.fetchone()

        if not account:
            bot.send_message(message.chat.id, f"Аккаунт с ID {account_id} не найден.")
            return

        cursor.execute(
            """
            SELECT login, path_to_maFile, password
            FROM accounts
            WHERE ID = ?
            """,
            (account_id,),
        )
        account = cursor.fetchone()

        if account is None:
            bot.send_message(message.chat.id, f"Аккаунт с ID {account_id} не найден.")
        else:
            login, path_to_maFile, current_password = account
            new_password = asyncio.run(
                changeSteamPassword(path_to_maFile, current_password)
            )

            cursor.execute(
                """
                UPDATE accounts
                SET password = ?
                WHERE login = ?
                """,
                (new_password, login),
            )
            conn.commit()

            bot.send_message(
                message.chat.id,
                f"Пароль для всех аккаунтов с логином '{login}' успешно изменен на {new_password}.",
            )
    finally:
        conn.close()
        clear_user_state(message.from_user.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_stop_rent_id"
)
def stop_rent_by_id_handler(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Пожалуйста, введите валидный числовой ID.")
        return

    account_id = int(message.text)
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT login
            FROM accounts
            WHERE ID = ?
            """,
            (account_id,),
        )
        result = cursor.fetchone()

        if not result:
            bot.send_message(
                message.chat.id,
                f"Аккаунт с ID {account_id} не найден.",
            )
            return

        login = result[0]

        cursor.execute(
            """
            UPDATE accounts
            SET owner = NULL, rental_start = NULL
            WHERE login = ?
            """,
            (login,),
        )

        if cursor.rowcount > 0:
            conn.commit()
            bot.send_message(
                message.chat.id,
                f"Аренда всех аккаунтов с логином '{login}' успешно остановлена.",
            )
        else:
            bot.send_message(
                message.chat.id,
                f"Аккаунты с логином '{login}' не найдены или аренда уже остановлена.",
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при остановке аренды: {str(e)}")
    finally:
        conn.close()
        clear_user_state(message.from_user.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_manual_rent_id"
)
def process_manual_rent_id(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Пожалуйста, введите валидный числовой ID.")
        return

    account_id = int(message.text)
    state_data = {"account_id": account_id}
    set_user_state(message.from_user.id, "waiting_for_manual_rent_owner", state_data)
    bot.send_message(message.chat.id, "Введите никнейм владельца для аренды.")

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_manual_rent_owner"
)
def process_manual_rent_owner(message):
    state_data = get_user_state(message.from_user.id)["data"]
    account_id = state_data["account_id"]
    owner_nickname = message.text

    try:
        if db_bot.set_account_owner(account_id, owner_nickname):
            account = db_bot.get_account_by_id(account_id)
            bot.send_message(
                message.chat.id,
                f"Аккаунт с ID {account_id} успешно передан в аренду пользователю '{owner_nickname}'.",
            )
            send_message_by_owner(
                owner=owner_nickname,
                message=(
                    f"Ваш аккаунт:\n"
                    f"📝 Уникальный ID: {account['id']}\n"
                    f"🔑 Название: `{account['account_name']}`\n"
                    f"⏱ Срок аренды: {account['rental_duration']} часа \n\n"
                    f"Логин: {account['login']}\n"
                    f"Пароль: {account['password']}\n\n"
                    f"Чтобы запросить код подтверждения, отправьте /code\n"
                    f"Чтобы задать вопрос, отправьте /question\n\n"
                    f"‼️За отзыв - вы получите дополнительные {HOURS_FOR_REVIEW} час/часа аренды.\n"
                    f"‼️ВАЖНО! Отзыв надо оставить до окончания вашей аренды.‼️\n\n"
                    f"------------------------------------------------------------------------------"
                ),
            )
        else:
            bot.send_message(
                message.chat.id,
                f"Не удалось найти аккаунт с ID {account_id} или установить владельца.",
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при установке владельца: {str(e)}")
    finally:
        clear_user_state(message.from_user.id)

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_extend_rental_id"
)
def process_extend_rental_id(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "Пожалуйста, введите валидный числовой ID.")
        return

    account_id = int(message.text)
    state_data = {"account_id": account_id}
    set_user_state(message.from_user.id, "waiting_for_extend_rental_duration", state_data)
    bot.send_message(message.chat.id, "На сколько часов вы хотите продлить аренду?")

@bot.message_handler(
    func=lambda message: get_user_state(message.from_user.id)["state"]
    == "waiting_for_extend_rental_duration"
)
def process_extend_rental_duration(message):
    if not message.text.isdigit() or int(message.text) <= 0:
        bot.send_message(message.chat.id, "Пожалуйста, введите положительное число часов.")
        return

    state_data = get_user_state(message.from_user.id)["data"]
    account_id = state_data["account_id"]
    duration_to_add = int(message.text)

    try:
        if db_bot.extend_rental_duration(account_id, duration_to_add):
            account = db_bot.get_account_by_id(account_id)
            bot.send_message(
                message.chat.id,
                f"‼️Аренда аккаунта с ID {account_id} успешно продлена на {duration_to_add} часов.\n"
                f"‼️Новый срок аренды: {account['rental_duration']} часов.\n"
                f"‼️Срок аренды: {account['rental_start']} - {account['rental_duration']} часов."
            )
            send_message_by_owner(
                owner=account["owner"],
                message=(
                    f"‼️Ваш аккаунт с ID {account_id} был продлен на {duration_to_add} часов.\n"
                    f"Новый срок аренды: {account['rental_duration']} часов.\n"
                    f"Срок аренды: {account['rental_start']} - {account['rental_duration']} часов."
                )
            )
        else:
            bot.send_message(
                message.chat.id,
                f"Не удалось найти аккаунт с ID {account_id} или продлить аренду.",
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при продлении аренды: {str(e)}")
    finally:
        clear_user_state(message.from_user.id)

def send_message_to_admin(message):
    bot.send_message(ADMIN_ID, message)

@bot.callback_query_handler(func=lambda call: call.data == "system_settings")
def system_settings_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    bot.edit_message_text(
        "⚙️ **Настройки системы:**\n\n"
        "Выберите параметр для настройки:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=get_system_settings_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "notification_settings")
def notification_settings_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    bot.edit_message_text(
        "📱 **Настройки уведомлений:**\n\n"
        "Выберите тип уведомлений для настройки:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=get_notification_settings_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "proxy_status")
def proxy_status_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    proxy_status = "✅ **Активен**" if PROXY_URL else "❌ **Не настроен**"
    proxy_info = f"🔌 **Прокси:** {proxy_status}\n"
    
    if PROXY_URL:
        proxy_info += f"🌐 **URL:** `{PROXY_URL}`\n"
        if PROXY_LOGIN:
            proxy_info += f"👤 **Логин:** `{PROXY_LOGIN}`\n"
    
    bot.edit_message_text(
        f"📊 **Статус прокси:**\n\n{proxy_info}",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=get_proxy_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "database_settings")
def database_settings_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    try:
        stats = db_bot.get_rental_statistics()
        db_info = (
            "🗄️ **Информация о базе данных:**\n\n"
            f"📊 **Размер:** `{stats.get('total_accounts', 0)}` записей\n"
            f"✅ **Статус:** Подключена\n"
            f"🔄 **Последнее обновление:** Только что\n\n"
            "💡 **Доступные операции:**\n"
            "• Резервное копирование\n"
            "• Очистка старых записей\n"
            "• Оптимизация"
        )
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("💾 Резервная копия", callback_data="db_backup"))
        keyboard.add(InlineKeyboardButton("🧹 Очистка", callback_data="db_cleanup"))
        keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="system_settings"))
        
        bot.edit_message_text(
            db_info,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)}")
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "auto_refresh_toggle")
def auto_refresh_toggle_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    bot.answer_callback_query(call.id, "Функция в разработке")

@bot.callback_query_handler(func=lambda call: call.data == "timeout_settings")
def timeout_settings_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    bot.answer_callback_query(call.id, "Функция в разработке")

@bot.callback_query_handler(func=lambda call: call.data == "multi_account_settings")
def multi_account_settings_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    current_setting = get_multi_account_setting()
    status_text = "✅ Включена" if current_setting else "❌ Выключена"
    
    bot.edit_message_text(
        f"🔢 **Настройки множественной выдачи аккаунтов:**\n\n"
        f"Текущий статус: {status_text}\n\n"
        f"**Описание:**\n"
        f"• Включена: Покупатель может получить несколько аккаунтов в одном заказе\n"
        f"• Выключена: Покупатель получает только 1 аккаунт независимо от количества в заказе\n\n"
        f"Выберите действие:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=get_multi_account_settings_keyboard()
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "multi_account_enable")
def multi_account_enable_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    if update_multi_account_setting(True):
        bot.answer_callback_query(call.id, "✅ Множественная выдача аккаунтов включена", show_alert=True)
        # Обновляем отображение
        multi_account_settings_callback(call)
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при сохранении настройки", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "multi_account_disable")
def multi_account_disable_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    if update_multi_account_setting(False):
        bot.answer_callback_query(call.id, "❌ Множественная выдача аккаунтов выключена", show_alert=True)
        # Обновляем отображение
        multi_account_settings_callback(call)
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка при сохранении настройки", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "multi_account_status")
def multi_account_status_callback(call):
    if call.from_user.id not in whitelisted_users:
        bot.answer_callback_query(call.id, "У вас нет доступа к этой функции")
        return
    
    current_setting = get_multi_account_setting()
    status_text = "✅ Включена" if current_setting else "❌ Выключена"
    
    # Получаем статистику
    try:
        stats = db_bot.get_rental_statistics()
        total_accounts = stats.get('total_accounts', 0)
        active_rentals = stats.get('active_rentals', 0)
        
        status_info = (
            f"📊 **Статус множественной выдачи:**\n\n"
            f"**Текущее состояние:** {status_text}\n\n"
            f"**Статистика системы:**\n"
            f"• Всего аккаунтов: `{total_accounts}`\n"
            f"• Активных аренд: `{active_rentals}`\n"
            f"• Свободных аккаунтов: `{total_accounts - active_rentals}`\n\n"
            f"**Как это работает:**\n"
            f"• При включенной настройке: покупатель получает столько аккаунтов, сколько заказал\n"
            f"• При выключенной настройке: покупатель получает только 1 аккаунт\n"
            f"• Настройка применяется к новым заказам\n"
            f"• Существующие аренды не изменяются"
        )
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔄 Обновить", callback_data="multi_account_status"))
        keyboard.add(InlineKeyboardButton("⬅️ Назад", callback_data="multi_account_settings"))
        
        bot.edit_message_text(
            status_info,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка получения статистики: {str(e)}")
    
    bot.answer_callback_query(call.id)

def main():
    bot.infinity_polling(none_stop=True, timeout=5)

if __name__ == "__main__":
    main()