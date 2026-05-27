import os
import logging
import requests
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Environment Variables ───────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN")
SMS_API_KEY = os.environ.get("SMS_API_KEY")
SMS_API_URL = os.environ.get("SMS_API_URL", "https://api.ivsms.com/v1")  # Override via env
OTP_GROUP_URL = os.environ.get("OTP_GROUP_URL", "https://t.me/your_otp_group")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")
if not SMS_API_KEY:
    raise RuntimeError("SMS_API_KEY environment variable is not set.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ─── In-Memory Session Store ─────────────────────────────────────────────────
# Structure: { chat_id: { "order_ids": [...], "numbers": [...], "country": "...", "country_flag": "..." } }
user_sessions: dict = {}

# ─── Country Config ──────────────────────────────────────────────────────────
COUNTRIES = [
    {"label": "🇵🇪 Peru",    "code": "PE", "service": "any", "price": "0.20TK", "flag": "🇵🇪"},
    {"label": "🇲🇲 Myanmar", "code": "MM", "service": "any", "price": "0.15TK", "flag": "🇲🇲"},
    {"label": "🇺🇸 USA",     "code": "US", "service": "any", "price": "0.50TK", "flag": "🇺🇸"},
    {"label": "🇬🇧 UK",      "code": "GB", "service": "any", "price": "0.45TK", "flag": "🇬🇧"},
    {"label": "🇮🇳 India",   "code": "IN", "service": "any", "price": "0.10TK", "flag": "🇮🇳"},
    {"label": "🇧🇷 Brazil",  "code": "BR", "service": "any", "price": "0.18TK", "flag": "🇧🇷"},
]

COUNTRY_MAP = {c["code"]: c for c in COUNTRIES}

# ─── SMS API Helper Functions ────────────────────────────────────────────────

def get_numbers(country_code: str, service: str, count: int = 3) -> list[dict]:
    """
    Request `count` virtual phone numbers from the SMS API.

    Expected API response per number (adapt to your provider):
        { "order_id": "abc123", "phone": "+51987654321", "status": "active" }

    Returns a list of dicts: [{"order_id": ..., "phone": ...}, ...]
    Returns an empty list on failure.
    """
    results = []
    for _ in range(count):
        try:
            resp = requests.get(
                f"{SMS_API_URL}/getNumber",
                params={
                    "api_key": SMS_API_KEY,
                    "country": country_code,
                    "service": service,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            # ── Adapt this block to your provider's actual response schema ──
            if data.get("status") == "success" or "order_id" in data:
                results.append({
                    "order_id": str(data["order_id"]),
                    "phone":    str(data["phone"]),
                })
            else:
                logger.warning("API returned unexpected payload: %s", data)
        except requests.RequestException as e:
            logger.error("get_numbers request failed: %s", e)

    return results


def check_otp(order_ids: list[str]) -> dict[str, str | None]:
    """
    Poll the SMS API for OTP codes for the given order IDs.

    Expected API response:
        { "order_id": "abc123", "status": "received", "sms_code": "483921" }
        or
        { "order_id": "abc123", "status": "waiting" }

    Returns a dict mapping order_id -> otp_code (or None if not yet received).
    """
    results = {}
    for order_id in order_ids:
        try:
            resp = requests.get(
                f"{SMS_API_URL}/getStatus",
                params={
                    "api_key": SMS_API_KEY,
                    "order_id": order_id,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            # ── Adapt this block to your provider's actual response schema ──
            if data.get("status") == "received" and data.get("sms_code"):
                results[order_id] = str(data["sms_code"])
            else:
                results[order_id] = None
        except requests.RequestException as e:
            logger.error("check_otp request failed for %s: %s", order_id, e)
            results[order_id] = None

    return results

# ─── UI Builders ─────────────────────────────────────────────────────────────

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("☎️ Get Number"),
        KeyboardButton("📨 Get Tempmail"),
        KeyboardButton("🔐 2FA"),
        KeyboardButton("👤 Fake Name"),
    )
    return kb


def country_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(c["label"], callback_data=f"country:{c['code']}")
        for c in COUNTRIES
    ]
    kb.add(*buttons)
    return kb


def number_keyboard(numbers: list[str], country_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)

    # One button per number — tapping copies it (switch_inline_query_current_chat trick)
    for num in numbers:
        kb.add(InlineKeyboardButton(f"📱 {num}", callback_data=f"copy:{num}"))

    # Control row
    kb.row(
        InlineKeyboardButton("🔔 OTP GROUP", url=OTP_GROUP_URL),
        InlineKeyboardButton("🔄 Change Number", callback_data=f"change:{country_code}"),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{country_code}"),
    )
    return kb


def activation_message(flag: str, country_label: str, price: str) -> str:
    return (
        "┌─── NUMBER VERIFIED SUCCESSFULLY ───┐\n"
        f">> {flag} {country_label} 🔥 ({price})\n"
        "└─── NUMBER VERIFIED SUCCESSFULLY ───┘"
    )

# ─── Handlers: Main Menu ──────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def handle_start(message):
    bot.send_message(
        message.chat.id,
        "👋 <b>Welcome!</b> Choose an option from the menu below:",
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "☎️ Get Number")
def handle_get_number(message):
    bot.send_message(
        message.chat.id,
        "🌍 <b>Select a Country</b> to get a virtual number:",
        reply_markup=country_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "📨 Get Tempmail")
def handle_tempmail(message):
    bot.send_message(
        message.chat.id,
        "📨 <b>Temp Mail</b> feature coming soon! Stay tuned.",
    )


@bot.message_handler(func=lambda m: m.text == "🔐 2FA")
def handle_2fa(message):
    bot.send_message(
        message.chat.id,
        "🔐 <b>2FA Generator</b> feature coming soon! Stay tuned.",
    )


@bot.message_handler(func=lambda m: m.text == "👤 Fake Name")
def handle_fake_name(message):
    bot.send_message(
        message.chat.id,
        "👤 <b>Fake Name Generator</b> feature coming soon! Stay tuned.",
    )

# ─── Handlers: Inline Callbacks ───────────────────────────────────────────────

def fetch_and_show_numbers(chat_id: int, country_code: str, message_id: int = None):
    """Fetch numbers from API and show/edit the activation message."""
    country = COUNTRY_MAP.get(country_code)
    if not country:
        bot.send_message(chat_id, "❌ Unknown country selected.")
        return

    # Send a loading indicator
    loading_msg = bot.send_message(chat_id, "⏳ Fetching numbers, please wait...")

    number_data = get_numbers(country_code, country["service"], count=3)

    if not number_data:
        bot.edit_message_text(
            "❌ <b>Failed to fetch numbers.</b>\n\nThe API may be temporarily unavailable. Please try again in a moment.",
            chat_id=chat_id,
            message_id=loading_msg.message_id,
        )
        return

    # Store session
    user_sessions[chat_id] = {
        "order_ids":    [n["order_id"] for n in number_data],
        "numbers":      [n["phone"]    for n in number_data],
        "country":      country["label"],
        "country_code": country_code,
        "country_flag": country["flag"],
        "price":        country["price"],
    }

    text = activation_message(country["flag"], country["label"], country["price"])
    kb   = number_keyboard([n["phone"] for n in number_data], country_code)

    bot.edit_message_text(
        text,
        chat_id=chat_id,
        message_id=loading_msg.message_id,
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("country:"))
def handle_country_select(call):
    country_code = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "⏳ Fetching numbers...")
    fetch_and_show_numbers(call.message.chat.id, country_code)


@bot.callback_query_handler(func=lambda c: c.data.startswith("change:"))
def handle_change_number(call):
    country_code = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "🔄 Fetching new numbers...")
    fetch_and_show_numbers(call.message.chat.id, country_code)


@bot.callback_query_handler(func=lambda c: c.data.startswith("refresh:"))
def handle_refresh(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)

    if not session:
        bot.answer_callback_query(call.id, "⚠️ No active session. Please request numbers first.")
        return

    bot.answer_callback_query(call.id, "🔍 Checking for OTP...")

    otp_results = check_otp(session["order_ids"])
    received    = {oid: code for oid, code in otp_results.items() if code}

    if received:
        lines = []
        for i, (oid, code) in enumerate(received.items()):
            phone = session["numbers"][session["order_ids"].index(oid)] if oid in session["order_ids"] else "Unknown"
            lines.append(f"📱 <b>{phone}</b>\n✅ OTP: <b><code>{code}</code></b>")

        otp_text = (
            "┌─── OTP RECEIVED ───┐\n"
            + "\n\n".join(lines) +
            "\n└─── OTP RECEIVED ───┘"
        )
        bot.send_message(chat_id, otp_text)
    else:
        bot.send_message(
            chat_id,
            "⏳ <b>No OTP received yet.</b>\n\nPlease wait a moment and tap <b>🔄 Refresh</b> again.",
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("copy:"))
def handle_copy_number(call):
    number = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, f"📋 {number}", show_alert=True)

# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Bot is starting (polling mode)...")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
