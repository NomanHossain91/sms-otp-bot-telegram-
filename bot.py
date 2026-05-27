"""
╔══════════════════════════════════════════════════════════╗
║           RAVEN SMS BOT — ivsms.com Session Scraper      ║
║   Deployment: Railway.app  |  Mode: worker               ║
║   Auth: Cookie-based HTTP session (no official API)      ║
╚══════════════════════════════════════════════════════════╝

Environment Variables Required (set in Railway → Variables):
    BOT_TOKEN           — Telegram bot token from @BotFather
    IVSMS_COOKIE        — Full cookie string from ivsms.com browser session
    IVSMS_USER_AGENT    — Browser User-Agent used when capturing the cookie
    OTP_GROUP_URL       — (optional) Telegram OTP group invite link
"""

import os
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Environment Variables ───────────────────────────────────────────────────
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
IVSMS_COOKIE     = os.environ.get("IVSMS_COOKIE", "")
IVSMS_USER_AGENT = os.environ.get(
    "IVSMS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
)
OTP_GROUP_URL = os.environ.get("OTP_GROUP_URL", "https://t.me/your_otp_group")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it to Railway → Variables.")
if not IVSMS_COOKIE:
    raise RuntimeError("IVSMS_COOKIE is not set. Add it to Railway → Variables.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ─── ivsms.com Scraper ───────────────────────────────────────────────────────

IVSMS_BASE    = "https://ivsms.com"
IVSMS_NUMBERS = f"{IVSMS_BASE}/dashboard/numbers"   # page listing purchased numbers
IVSMS_INBOX   = f"{IVSMS_BASE}/dashboard/inbox"     # page showing incoming SMS / OTP


def _build_headers() -> dict:
    """Construct browser-like headers using the stored session cookie."""
    return {
        "Cookie":           IVSMS_COOKIE,
        "User-Agent":       IVSMS_USER_AGENT,
        "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":  "en-US,en;q=0.9",
        "Accept-Encoding":  "gzip, deflate, br",
        "Referer":          IVSMS_BASE,
        "Connection":       "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _get(url: str, params: dict = None) -> requests.Response | None:
    """Authenticated GET request. Returns Response or None on failure."""
    try:
        resp = requests.get(
            url,
            headers=_build_headers(),
            params=params,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp
        logger.warning("GET %s returned HTTP %s", url, resp.status_code)
    except requests.RequestException as exc:
        logger.error("GET %s failed: %s", url, exc)
    return None


class IVSMSScraper:
    """
    Scrapes ivsms.com authenticated dashboard pages to:
      1. Retrieve active phone numbers filtered by country.
      2. Check incoming SMS / OTP for a specific number.

    ──────────────────────────────────────────────────────
    HOW TO OBTAIN YOUR COOKIE (one-time setup):
      1. Open Chrome → Log in to ivsms.com
      2. Press F12 → Network tab → Refresh page
      3. Click any request to ivsms.com → Headers → Request Headers
      4. Copy the entire 'Cookie:' header value
      5. Paste it into Railway → Variables as IVSMS_COOKIE
    ──────────────────────────────────────────────────────

    NOTE: The CSS selectors / table column indices below are based on
    ivsms.com's HTML as observed. If the site updates its layout,
    adjust NUMBERS_TABLE_SELECTOR and SMS_TABLE_SELECTOR accordingly.
    """

    # CSS selectors — update these if ivsms.com changes their HTML structure
    NUMBERS_TABLE_SELECTOR = "table"          # main numbers table
    SMS_TABLE_SELECTOR     = "table"          # inbox / OTP table

    # Country name → partial string that appears in ivsms.com's country column
    COUNTRY_KEYWORDS = {
        "PE": ["peru",    "perú"],
        "MM": ["myanmar", "burma"],
        "US": ["united states", "usa", "us"],
        "GB": ["united kingdom", "uk", "great britain"],
        "IN": ["india"],
        "BR": ["brazil", "brasil"],
    }

    def get_numbers_by_country(self, country_code: str, limit: int = 3) -> list[dict]:
        """
        Scrape the numbers dashboard and return up to `limit` active numbers
        matching the given country code.

        Returns list of dicts: [{"phone": "+51987654321", "sid": "...", "status": "active"}, ...]
        """
        resp = _get(IVSMS_NUMBERS)
        if not resp:
            logger.error("Could not reach ivsms.com numbers page.")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.select_one(self.NUMBERS_TABLE_SELECTOR)
        if not table:
            logger.error("Numbers table not found on ivsms.com page. Selector may need updating.")
            return []

        keywords = self.COUNTRY_KEYWORDS.get(country_code.upper(), [country_code.lower()])
        results  = []

        rows = table.find_all("tr")
        for row in rows[1:]:  # skip header row
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if not cols:
                continue

            # ── Adapt column indices to match ivsms.com's actual table layout ──
            # Typical layout: [#, Phone Number, Country, Service, Status, Expires, Action]
            if len(cols) < 3:
                continue

            # Try to find phone and country from the row
            row_text = " ".join(cols).lower()
            phone    = self._extract_phone(cols)
            country_match = any(kw in row_text for kw in keywords)

            # Filter by country and active status
            status_text = row_text
            is_active   = "active" in status_text or "online" in status_text or "waiting" in status_text

            if phone and country_match and is_active:
                sid = self._extract_sid(row)
                results.append({
                    "phone":   phone,
                    "sid":     sid or phone,   # fallback to phone if no explicit SID
                    "status":  "active",
                    "country": country_code,
                })
                if len(results) >= limit:
                    break

        logger.info("Found %d numbers for country %s", len(results), country_code)
        return results

    def check_otp(self, phone: str) -> str | None:
        """
        Scrape the ivsms.com inbox/SMS dashboard for the latest OTP
        associated with the given phone number.

        Returns the OTP string if found, otherwise None.
        """
        resp = _get(IVSMS_INBOX)
        if not resp:
            logger.error("Could not reach ivsms.com inbox page.")
            return None

        soup  = BeautifulSoup(resp.text, "html.parser")
        table = soup.select_one(self.SMS_TABLE_SELECTOR)
        if not table:
            logger.error("SMS inbox table not found on ivsms.com. Selector may need updating.")
            return None

        # Normalise the phone number for matching (digits only)
        phone_digits = re.sub(r"\D", "", phone)

        rows = table.find_all("tr")
        for row in rows[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if not cols:
                continue

            row_text = " ".join(cols)
            # Match by phone number digits anywhere in the row
            if phone_digits and phone_digits in re.sub(r"\D", "", row_text):
                otp = self._extract_otp_from_text(row_text)
                if otp:
                    logger.info("OTP found for %s: %s", phone, otp)
                    return otp

        return None

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_phone(cols: list[str]) -> str | None:
        """Find the column that looks like a phone number."""
        phone_pattern = re.compile(r"\+?\d[\d\s\-]{7,14}\d")
        for col in cols:
            match = phone_pattern.search(col.replace(" ", ""))
            if match:
                # Return clean E.164-ish string
                return re.sub(r"[\s\-]", "", match.group())
        return None

    @staticmethod
    def _extract_sid(row) -> str | None:
        """Try to extract an SID from a data attribute or hidden input in the row."""
        for tag in row.find_all(True):
            for attr in ("data-id", "data-sid", "data-number", "value"):
                val = tag.get(attr, "")
                if val and re.match(r"[\w\-]{4,}", val):
                    return val
        return None

    @staticmethod
    def _extract_otp_from_text(text: str) -> str | None:
        """
        Extract a numeric OTP from a block of text.
        Handles 4–8 digit codes, including ones preceded by keywords.
        """
        # Priority: explicit keyword patterns
        patterns = [
            r"(?:code|otp|pin|verification|verif|passcode)[^\d]*(\d{4,8})",
            r"(\d{6})",   # most OTPs are 6 digits
            r"(\d{4,8})", # fallback: any 4-8 digit sequence
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None


# Singleton scraper instance
scraper = IVSMSScraper()

# ─── Session Store ───────────────────────────────────────────────────────────
# { chat_id: { "numbers": [...], "country_code": "PE", "country_label": "...", ... } }
user_sessions: dict = {}

# ─── Country Config ──────────────────────────────────────────────────────────
COUNTRIES = [
    {"label": "🇵🇪 Peru",    "code": "PE", "flag": "🇵🇪", "price": "0.20TK"},
    {"label": "🇲🇲 Myanmar", "code": "MM", "flag": "🇲🇲", "price": "0.15TK"},
    {"label": "🇺🇸 USA",     "code": "US", "flag": "🇺🇸", "price": "0.50TK"},
    {"label": "🇬🇧 UK",      "code": "GB", "flag": "🇬🇧", "price": "0.45TK"},
    {"label": "🇮🇳 India",   "code": "IN", "flag": "🇮🇳", "price": "0.10TK"},
    {"label": "🇧🇷 Brazil",  "code": "BR", "flag": "🇧🇷", "price": "0.18TK"},
]
COUNTRY_MAP = {c["code"]: c for c in COUNTRIES}

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
    kb.add(*[
        InlineKeyboardButton(c["label"], callback_data=f"country:{c['code']}")
        for c in COUNTRIES
    ])
    return kb


def number_buttons_keyboard(numbers: list[dict], country_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for entry in numbers:
        kb.add(InlineKeyboardButton(f"📱 {entry['phone']}", callback_data=f"copy:{entry['phone']}"))
    kb.row(
        InlineKeyboardButton("🔔 OTP GROUP",      url=OTP_GROUP_URL),
        InlineKeyboardButton("🔄 Change Number",  callback_data=f"change:{country_code}"),
        InlineKeyboardButton("🔄 Refresh",         callback_data=f"refresh:{country_code}"),
    )
    return kb


def activation_text(flag: str, label: str, price: str) -> str:
    return (
        "┌─── NUMBER VERIFIED SUCCESSFULLY ───┐\n"
        f">> {flag} {label} 🔥 ({price})\n"
        "└─── NUMBER VERIFIED SUCCESSFULLY ───┘"
    )

# ─── Core: Fetch Numbers & Show ──────────────────────────────────────────────

def fetch_and_display_numbers(chat_id: int, country_code: str):
    """Scrape ivsms.com for numbers, store in session, and send to user."""
    country = COUNTRY_MAP.get(country_code)
    if not country:
        bot.send_message(chat_id, "❌ Unknown country.")
        return

    loading = bot.send_message(chat_id, "⏳ Connecting to ivsms.com and fetching numbers...")

    numbers = scraper.get_numbers_by_country(country_code, limit=3)

    if not numbers:
        bot.edit_message_text(
            "❌ <b>No active numbers found</b> for this country right now.\n\n"
            "Possible reasons:\n"
            "• Your session cookie has expired — update <code>IVSMS_COOKIE</code> in Railway\n"
            "• No numbers are active in your ivsms.com pool for this country\n"
            "• ivsms.com changed their HTML structure (contact dev to update selectors)",
            chat_id=chat_id,
            message_id=loading.message_id,
        )
        return

    # Save session
    user_sessions[chat_id] = {
        "numbers":       numbers,
        "country_code":  country_code,
        "country_label": country["label"],
        "country_flag":  country["flag"],
        "price":         country["price"],
        "fetched_at":    time.time(),
    }

    text = activation_text(country["flag"], country["label"], country["price"])
    kb   = number_buttons_keyboard(numbers, country_code)

    bot.edit_message_text(
        text,
        chat_id=chat_id,
        message_id=loading.message_id,
        reply_markup=kb,
    )

# ─── Message Handlers ────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def handle_start(msg):
    bot.send_message(
        msg.chat.id,
        "👋 <b>Welcome to Raven SMS Bot!</b>\n\nChoose an option below:",
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "☎️ Get Number")
def handle_get_number(msg):
    bot.send_message(
        msg.chat.id,
        "🌍 <b>Select a Country</b> to fetch a virtual number:",
        reply_markup=country_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "📨 Get Tempmail")
def handle_tempmail(msg):
    bot.send_message(msg.chat.id, "📨 <b>Temp Mail</b> — Coming soon!")


@bot.message_handler(func=lambda m: m.text == "🔐 2FA")
def handle_2fa(msg):
    bot.send_message(msg.chat.id, "🔐 <b>2FA Generator</b> — Coming soon!")


@bot.message_handler(func=lambda m: m.text == "👤 Fake Name")
def handle_fake_name(msg):
    bot.send_message(msg.chat.id, "👤 <b>Fake Name Generator</b> — Coming soon!")

# ─── Callback Handlers ────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("country:"))
def cb_country(call):
    country_code = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "⏳ Fetching from ivsms.com...")
    fetch_and_display_numbers(call.message.chat.id, country_code)


@bot.callback_query_handler(func=lambda c: c.data.startswith("change:"))
def cb_change(call):
    country_code = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, "🔄 Fetching new numbers...")
    fetch_and_display_numbers(call.message.chat.id, country_code)


@bot.callback_query_handler(func=lambda c: c.data.startswith("refresh:"))
def cb_refresh(call):
    chat_id = call.message.chat.id
    session = user_sessions.get(chat_id)

    if not session:
        bot.answer_callback_query(call.id, "⚠️ No active session. Please request numbers first.")
        return

    bot.answer_callback_query(call.id, "🔍 Checking for OTP...")

    found_any = False
    otp_lines = []

    for entry in session["numbers"]:
        phone = entry["phone"]
        otp   = scraper.check_otp(phone)
        if otp:
            otp_lines.append(
                f"📱 <b>{phone}</b>\n"
                f"✅ OTP: <b><code>{otp}</code></b>"
            )
            found_any = True
        else:
            otp_lines.append(
                f"📱 <b>{phone}</b>\n"
                f"⏳ No OTP yet"
            )

    if found_any:
        header = "┌─── OTP RECEIVED ───┐\n"
        footer = "\n└─── OTP RECEIVED ───┘"
        bot.send_message(chat_id, header + "\n\n".join(otp_lines) + footer)
    else:
        bot.send_message(
            chat_id,
            "⏳ <b>No OTP received yet.</b>\n\n"
            "Please wait a moment and tap <b>🔄 Refresh</b> again.",
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("copy:"))
def cb_copy(call):
    number = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id, f"📋 {number}", show_alert=True)

# ─── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("🚀 Raven SMS Bot starting (long-polling)...")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
