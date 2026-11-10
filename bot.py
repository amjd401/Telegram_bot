# -*- coding: utf-8 -*-
"""
بوت حماية وإدارة مجموعات تلقرام
المكتبة: python-telegram-bot (الإصدار 21 أو أحدث)
التشغيل:  python bot.py   (أو تلقائياً من GitHub Actions عبر .github/workflows/bot.yml)
البيانات: data.json   |   كلمات ممنوعة عامة: words.txt
"""
import html
import json
import logging
import os
import random
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ====================== الإعدادات الأساسية ======================
TOKEN = os.getenv("BOT_TOKEN", "ضع_التوكن_هنا")      # التوكن من @BotFather
OWNER_ID = int(os.getenv("OWNER_ID") or 0)            # ايديك انت (المطور)
DB_PATH = os.getenv("DB_PATH", ":memory:")
DATA_FILE = os.getenv("DATA_FILE", "data.json")       # هنا تنحفظ كل بيانات البوت
WORDS_FILE = os.getenv("WORDS_FILE", "words.txt")     # كلمات ممنوعة لكل المجموعات
RUN_SECONDS = int(os.getenv("RUN_SECONDS") or 0)      # مدة التشغيل ثم يقفل نفسه (0 = دايم)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

HTML = ParseMode.HTML

# ====================== الأقفال ======================
# المفتاح : الاسم (مكتوب بالصيغة الموحدة: ه بدل ة ، ا بدل أ إ آ)
LOCKS = {
    "links": "الروابط",
    "usernames": "المعرفات",
    "forward": "التوجيه",
    "photo": "الصور",
    "video": "الفيديو",
    "sticker": "الملصقات",
    "gif": "المتحركه",
    "voice": "الصوتيات",
    "document": "الملفات",
    "contact": "الجهات",
    "location": "المواقع",
    "poll": "الاستفتاءات",
    "inline": "الانلاين",
    "english": "الانجليزيه",
    "long": "الكلايش",
    "edit": "التعديل",
    "channels": "القنوات",
    "chat": "الدردشه",
    "bots": "البوتات",
    "joins": "الدخول",
    "tgservice": "الاشعارات",
    "flood": "التكرار",
    "captcha": "التحقق",
    "raid": "الهجمات",
    "names": "الاسماء",
    "mentions": "المنشن",
    "newbies": "الجدد",
    "persian": "الفارسيه",
}
NAME_TO_LOCK = {v: k for k, v in LOCKS.items()}
DEFAULT_ON = {
    "links", "bots", "flood", "channels", "raid",
    "captcha", "names", "mentions", "newbies",
}
CAPTCHA_SECONDS = 120
LOCK_ALL = [
    "links", "usernames", "forward", "photo", "video", "sticker", "gif",
    "voice", "document", "contact", "location", "poll", "inline",
]

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/|tg://|"
    r"\b[\w-]+\.(com|net|org|me|io|co|xyz|info|link|ly|gg|tv|app|site|online|store|shop|top|club)\b)",
    re.IGNORECASE,
)
USERNAME_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_]{3,}")
ENGLISH_RE = re.compile(r"[A-Za-z]{3,}")
TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0640]")
DURATION_RE = re.compile(r"(\d+)\s*(دقيقه|دقايق|دقائق|ساعه|ساعات|يوم|ايام|د|س|ي)")

_NORM = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"})


def norm(text: str) -> str:
    """توحيد الحروف (نفس الطول) عشان الأوامر تشتغل بأي كتابة."""
    return (text or "").translate(_NORM).lower()


ZW_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
PUNCT_RE = re.compile(r"[._\-*~|/\\]+")
REPEAT_RE = re.compile(r"(.)\1{2,}")
PERSIAN_RE = re.compile(r"[پچژگکی]")


def clean(text: str) -> str:
    """توحيد + حذف التشكيل والحروف المخفية والرموز والتكرار (لكشف التحايل على الفلتر)."""
    t = TASHKEEL_RE.sub("", norm(text))
    t = PUNCT_RE.sub("", ZW_RE.sub("", t))
    return REPEAT_RE.sub(r"\1", t)


def mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(name or "عضو")}</a>'


# ====================== قاعدة البيانات ======================
db = sqlite3.connect(DB_PATH)
db.execute("PRAGMA journal_mode=WAL")
db.executescript(
    """
CREATE TABLE IF NOT EXISTS settings(chat_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(chat_id, key));
CREATE TABLE IF NOT EXISTS replies(chat_id INTEGER, trig TEXT, response TEXT, PRIMARY KEY(chat_id, trig));
CREATE TABLE IF NOT EXISTS badwords(chat_id INTEGER, word TEXT, PRIMARY KEY(chat_id, word));
CREATE TABLE IF NOT EXISTS warns(chat_id INTEGER, user_id INTEGER, count INTEGER, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS ranks(chat_id INTEGER, user_id INTEGER, rank TEXT, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS members(chat_id INTEGER, user_id INTEGER, msgs INTEGER, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY, username TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, title TEXT);
CREATE TABLE IF NOT EXISTS joined(chat_id INTEGER, user_id INTEGER, ts REAL, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS gbans(user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS pending(chat_id INTEGER, user_id INTEGER, mid INTEGER, ans INTEGER, ts REAL, PRIMARY KEY(chat_id, user_id));
"""
)
db.commit()

# الجداول اللي تنحفظ في data.json (جدول users فيه أسماء الأعضاء فما ينحفظ)
SAVED_TABLES = ["settings", "replies", "badwords", "warns", "ranks", "members",
                "chats", "joined", "gbans", "pending"]


def load_data() -> None:
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logging.warning("ما قدرت أقرأ %s: %s", DATA_FILE, e)
        return
    for table in SAVED_TABLES:
        rows = data.get(table) or []
        if rows:
            marks = ",".join("?" * len(rows[0]))
            db.executemany(f"INSERT OR REPLACE INTO {table} VALUES({marks})", rows)
    db.commit()


def save_data() -> None:
    data = {t: db.execute(f"SELECT * FROM {t}").fetchall() for t in SAVED_TABLES}
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, DATA_FILE)


def load_words() -> set:
    words = set()
    if os.path.exists(WORDS_FILE):
        with open(WORDS_FILE, encoding="utf-8") as f:
            for line in f:
                w = clean(line.strip())
                if len(w) >= 2 and not line.lstrip().startswith("#"):
                    words.add(w)
    return words


load_data()
GLOBAL_WORDS = load_words()

# كاش بالذاكرة لتخفيف الضغط على القاعدة
SETTINGS: dict = {}
REPLIES: dict = {}
BADWORDS: dict = {}
RANKS: dict = {}
ADMINS: dict = {}          # chat_id -> (وقت, {user_id: status})
STATE: dict = {}           # (chat, user) -> حالة اضافة رد
FLOOD = defaultdict(lambda: deque(maxlen=30))
LAST_ALBUM: dict = {}
JOINS = defaultdict(lambda: deque(maxlen=50))
PENDING: dict = {}         # (chat, user) -> {mid, ans, tries, ts} حق التحقق
LAST_NOTICE: dict = {}
JOINED: dict = {}          # (chat, user) -> وقت الدخول
SEEN_JOIN: dict = {}
LAST_TEXT: dict = {}
FLOOD_HIT: dict = {}
STRIKES = defaultdict(lambda: deque(maxlen=10))
GBANS: set = {r[0] for r in db.execute("SELECT user_id FROM gbans").fetchall()}


def is_newbie(chat_id: int, uid: int) -> bool:
    key = (chat_id, uid)
    if key not in JOINED:
        row = db.execute(
            "SELECT ts FROM joined WHERE chat_id=? AND user_id=?", key
        ).fetchone()
        JOINED[key] = row[0] if row else 0
    hours = int(get_s(chat_id, "newbie_hours", 24))
    return time.time() - JOINED[key] < hours * 3600


def settings(chat_id: int) -> dict:
    if chat_id not in SETTINGS:
        rows = db.execute("SELECT key, value FROM settings WHERE chat_id=?", (chat_id,))
        SETTINGS[chat_id] = dict(rows.fetchall())
    return SETTINGS[chat_id]


def get_s(chat_id: int, key: str, default=None):
    return settings(chat_id).get(key, default)


def set_s(chat_id: int, key: str, value) -> None:
    settings(chat_id)[key] = str(value)
    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (chat_id, key, str(value)))
    db.commit()


def locked(chat_id: int, key: str) -> bool:
    v = get_s(chat_id, "lock_" + key)
    return v == "1" if v is not None else key in DEFAULT_ON


def set_lock(chat_id: int, key: str, on: bool) -> None:
    set_s(chat_id, "lock_" + key, "1" if on else "0")


def replies(chat_id: int) -> dict:
    if chat_id not in REPLIES:
        rows = db.execute("SELECT trig, response FROM replies WHERE chat_id=?", (chat_id,))
        REPLIES[chat_id] = dict(rows.fetchall())
    return REPLIES[chat_id]


def badwords(chat_id: int) -> set:
    if chat_id not in BADWORDS:
        rows = db.execute("SELECT word FROM badwords WHERE chat_id=?", (chat_id,))
        BADWORDS[chat_id] = {r[0] for r in rows.fetchall()}
    return BADWORDS[chat_id]


def ranks(chat_id: int) -> dict:
    if chat_id not in RANKS:
        rows = db.execute("SELECT user_id, rank FROM ranks WHERE chat_id=?", (chat_id,))
        RANKS[chat_id] = dict(rows.fetchall())
    return RANKS[chat_id]


def get_warns(chat_id: int, uid: int) -> int:
    row = db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, uid)
    ).fetchone()
    return row[0] if row else 0


def set_warns(chat_id: int, uid: int, n: int) -> None:
    if n <= 0:
        db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, uid))
    else:
        db.execute("INSERT OR REPLACE INTO warns VALUES(?,?,?)", (chat_id, uid, n))
    db.commit()


def track(chat, user, count: bool) -> None:
    db.execute("INSERT OR REPLACE INTO chats VALUES(?,?)", (chat.id, chat.title or ""))
    if user:
        db.execute(
            "INSERT OR REPLACE INTO users VALUES(?,?,?)",
            (user.id, (user.username or "").lower(), user.full_name),
        )
        if count:
            db.execute(
                "INSERT INTO members VALUES(?,?,1) ON CONFLICT(chat_id,user_id) "
                "DO UPDATE SET msgs=msgs+1",
                (chat.id, user.id),
            )
    db.commit()


# ====================== الصلاحيات ======================
LEVEL_NAMES = {0: "عضو", 1: "مميز ⭐", 2: "ادمن 👮", 3: "المالك 👑", 4: "المطور 🛠"}


async def tg_admins(context, chat_id: int, force: bool = False) -> dict:
    cached = ADMINS.get(chat_id)
    if cached and not force and time.time() - cached[0] < 300:
        return cached[1]
    try:
        lst = await context.bot.get_chat_administrators(chat_id)
        data = {m.user.id: m.status for m in lst}
    except TelegramError:
        data = cached[1] if cached else {}
    ADMINS[chat_id] = (time.time(), data)
    return data


async def level(context, chat_id: int, uid: int) -> int:
    """0 عضو | 1 مميز | 2 ادمن | 3 مالك المجموعة | 4 المطور"""
    if OWNER_ID and uid == OWNER_ID:
        return 4
    status = (await tg_admins(context, chat_id)).get(uid)
    if status == "creator":
        return 3
    if status:
        return 2
    return {"admin": 2, "vip": 1}.get(ranks(chat_id).get(uid), 0)


# ====================== أدوات مساعدة ======================
async def _del_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, mid = context.job.data
    try:
        await context.bot.delete_message(chat_id, mid)
    except TelegramError:
        pass


async def notice(context, chat_id: int, text: str, ttl: int = 20) -> None:
    """رسالة تنبيه تنحذف تلقائياً بعد ttl ثانية (0 = ما تنحذف)."""
    try:
        m = await context.bot.send_message(chat_id, text, parse_mode=HTML)
    except TelegramError:
        return
    if ttl and context.job_queue:
        context.job_queue.run_once(_del_job, ttl, data=(chat_id, m.message_id))


async def attempt(msg, coro) -> bool:
    """ينفذ الإجراء ويبلغ اذا البوت ما عنده صلاحية."""
    try:
        await coro
        return True
    except (BadRequest, Forbidden) as e:
        await msg.reply_text(
            f"⚠️ ما قدرت أنفذ. تأكد إني مشرف وعندي الصلاحيات.\n<code>{html.escape(str(e))}</code>",
            parse_mode=HTML,
        )
        return False


async def mute(bot, chat_id: int, uid: int, seconds: int = 0) -> None:
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None
    await bot.restrict_chat_member(
        chat_id, uid, ChatPermissions.no_permissions(), until_date=until
    )


async def unmute(bot, chat_id: int, uid: int) -> None:
    await bot.restrict_chat_member(chat_id, uid, ChatPermissions.all_permissions())


async def kick(bot, chat_id: int, uid: int) -> None:
    await bot.ban_chat_member(chat_id, uid)
    await bot.unban_chat_member(chat_id, uid, only_if_banned=True)


def parse_duration(text: str) -> int:
    m = DURATION_RE.search(norm(text))
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith("د"):
        return n * 60
    if unit.startswith("س"):
        return n * 3600
    return n * 86400


def fmt_duration(sec: int) -> str:
    if not sec:
        return "دائم"
    if sec % 86400 == 0:
        return f"{sec // 86400} يوم"
    if sec % 3600 == 0:
        return f"{sec // 3600} ساعة"
    return f"{max(1, sec // 60)} دقيقة"


async def get_target(update: Update, arg: str):
    """يرجع (ايدي, اسم, باقي النص) من الرد أو الايدي أو المعرف."""
    msg = update.effective_message
    r = msg.reply_to_message
    if r and r.from_user and not r.forum_topic_created:
        return r.from_user.id, r.from_user.full_name, arg
    for e in msg.entities or []:
        if e.type == "text_mention" and e.user:
            return e.user.id, e.user.full_name, ""
    parts = arg.split(maxsplit=1)
    if not parts:
        return None, None, arg
    first, rest = parts[0], parts[1] if len(parts) > 1 else ""
    if first.isdigit():
        row = db.execute("SELECT name FROM users WHERE user_id=?", (int(first),)).fetchone()
        return int(first), row[0] if row else first, rest
    if first.startswith("@"):
        row = db.execute(
            "SELECT user_id, name FROM users WHERE username=?", (first[1:].lower(),)
        ).fetchone()
        if row:
            return row[0], row[1], rest
    return None, None, arg


async def need_target(update, context, arg, lvl):
    """يجيب الهدف ويتأكد إن رتبته أقل من المنفذ."""
    msg = update.effective_message
    uid, name, rest = await get_target(update, arg)
    if not uid:
        await msg.reply_text("↩️ رد على رسالة العضو، أو اكتب الايدي / المعرف بعد الأمر.")
        return None
    if uid == context.bot.id:
        await msg.reply_text("😅 ما أقدر أنفذ الأمر على نفسي.")
        return None
    if await level(context, msg.chat_id, uid) >= lvl:
        await msg.reply_text("🚫 ما تقدر تنفذ الأمر على شخص رتبته مثلك أو أعلى.")
        return None
    return uid, name, rest


# ====================== التحذيرات والعقوبات ======================
async def add_warn(context, chat_id: int, uid: int, name: str, reason: str) -> None:
    n = get_warns(chat_id, uid) + 1
    limit = int(get_s(chat_id, "warn_limit", 3))
    who = mention(uid, name)
    if n < limit:
        set_warns(chat_id, uid, n)
        await notice(context, chat_id, f"⚠️ تحذير {n}/{limit} للعضو {who}\nالسبب: {reason}", 0)
        return
    set_warns(chat_id, uid, 0)
    action = get_s(chat_id, "warn_action", "mute")
    try:
        if action == "ban":
            await context.bot.ban_chat_member(chat_id, uid)
            done = "تم حظره 🚫"
        elif action == "kick":
            await kick(context.bot, chat_id, uid)
            done = "تم طرده 👢"
        else:
            await mute(context.bot, chat_id, uid, 86400)
            done = "تم كتمه يوم كامل 🔇"
    except TelegramError:
        done = "لكن ما عندي صلاحية أعاقبه"
    await notice(context, chat_id, f"⛔️ {who} وصل حد التحذيرات ({limit})، {done}", 0)


async def punish(update: Update, context, reason: str) -> None:
    msg = update.effective_message
    chat_id = msg.chat_id
    try:
        await msg.delete()
    except TelegramError:
        return
    user = msg.from_user
    if not user or msg.sender_chat:
        return
    penalty = get_s(chat_id, "penalty", "delete")
    if penalty == "warn":
        await add_warn(context, chat_id, user.id, user.full_name, f"مخالفة قفل {reason}")
        return
    key = (chat_id, user.id)
    if penalty == "mute":
        try:
            await mute(context.bot, chat_id, user.id, 600)
        except TelegramError:
            pass
        await notice(
            context, chat_id,
            f"🔇 {mention(user.id, user.full_name)} انكتم 10 دقايق — ممنوع {reason}",
        )
        return
    now = time.time()
    dq = STRIKES[key]
    dq.append(now)
    if len([t for t in dq if now - t <= 600]) >= 4:  # 4 مخالفات خلال 10 دقايق
        dq.clear()
        try:
            await mute(context.bot, chat_id, user.id, 3600)
        except TelegramError:
            pass
        await notice(
            context, chat_id,
            f"🔇 {mention(user.id, user.full_name)} انكتم ساعة بسبب تكرار المخالفات.", 60,
        )
        return
    if time.time() - LAST_NOTICE.get(key, 0) > 60:   # تنبيه واحد بالدقيقة لكل عضو
        LAST_NOTICE[key] = time.time()
        await notice(
            context, chat_id,
            f"🚫 {mention(user.id, user.full_name)} ممنوع {reason} في المجموعة.", 10,
        )


def violation(msg, chat_id: int, edited: bool, uid: int = 0):
    """يرجع اسم القفل اللي خالفته الرسالة، أو None."""
    text = msg.text or msg.caption or ""
    ents = list(msg.entities or []) + list(msg.caption_entities or [])
    L = lambda k: locked(chat_id, k)  # noqa: E731

    if L("chat"):
        return "الكلام (الدردشة مقفلة)"
    if edited and L("edit"):
        return "التعديل"
    if L("links") and (
        any(e.type in ("url", "text_link") for e in ents) or LINK_RE.search(text)
    ):
        return "الروابط"
    if L("usernames") and (
        any(e.type == "mention" for e in ents) or USERNAME_RE.search(text)
    ):
        return "المعرفات"
    if L("mentions") and sum(e.type in ("mention", "text_mention") for e in ents) > 5:
        return "المنشن الجماعي"
    forwarded = (
        getattr(msg, "forward_origin", None)
        or getattr(msg, "external_reply", None)
        or getattr(msg, "story", None)
    )
    if L("forward") and forwarded:
        return "التوجيه"
    if L("persian") and PERSIAN_RE.search(text):
        return "الفارسية"
    if uid and L("newbies") and is_newbie(chat_id, uid) and (
        msg.photo or msg.video or msg.video_note or msg.animation or msg.document
        or msg.contact or forwarded or LINK_RE.search(text)
        or any(e.type in ("url", "text_link", "mention") for e in ents)
    ):
        return "إرسال الوسائط والروابط للأعضاء الجدد"
    if L("photo") and msg.photo:
        return "الصور"
    if L("video") and (msg.video or msg.video_note):
        return "الفيديو"
    if L("sticker") and msg.sticker:
        return "الملصقات"
    if L("gif") and msg.animation:
        return "المتحركة"
    if L("voice") and (msg.voice or msg.audio):
        return "الصوتيات"
    if L("document") and msg.document and not msg.animation:
        return "الملفات"
    if L("contact") and msg.contact:
        return "الجهات"
    if L("location") and (msg.location or msg.venue):
        return "المواقع"
    if L("poll") and msg.poll:
        return "الاستفتاءات"
    if L("inline") and msg.via_bot:
        return "الانلاين"
    if L("english") and ENGLISH_RE.search(text):
        return "الانجليزية"
    if L("long") and len(text) > int(get_s(chat_id, "long_limit", 800)):
        return "الكلايش الطويلة"
    words = badwords(chat_id)
    if text and (words or GLOBAL_WORDS):
        c = clean(text)
        if any(w in c for w in words) or any(w in c for w in GLOBAL_WORDS):
            return "الكلمات الممنوعة"
    return None


def is_flooding(chat_id: int, msg) -> bool:
    uid = msg.from_user.id
    key = (chat_id, uid)
    if msg.media_group_id:                    # الألبوم ينحسب رسالة وحدة
        if LAST_ALBUM.get(key) == msg.media_group_id:
            return False
        LAST_ALBUM[key] = msg.media_group_id
    now = time.time()
    dq = FLOOD[key]
    dq.append(now)
    limit = int(get_s(chat_id, "flood_limit", 6))
    recent = [t for t in dq if now - t <= 5]
    if len(recent) >= limit:
        dq.clear()
        return True
    t = clean(getattr(msg, "text", None) or getattr(msg, "caption", None) or "")
    if t:                                     # نفس الرسالة 4 مرات خلال دقيقة
        last = LAST_TEXT.get(key)
        cnt = last[1] + 1 if last and last[0] == t and now - last[2] <= 60 else 1
        LAST_TEXT[key] = (t, cnt, now)
        if cnt >= 4:
            LAST_TEXT.pop(key, None)
            return True
    return False


# ====================== لوحة التحكم ======================
def panel(chat_id: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for key, name in LOCKS.items():
        icon = "🔒" if locked(chat_id, key) else "🔓"
        row.append(InlineKeyboardButton(f"{icon} {name}", callback_data=f"lk:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✖️ إغلاق", callback_data="close")])
    return InlineKeyboardMarkup(rows)


PANEL_TEXT = "⚙️ <b>لوحة الحماية</b>\n🔒 مقفول   🔓 مفتوح\nاضغط على أي خيار لتغييره:"

HELP = """📖 <b>أوامر البوت</b>

🛡 <b>الحماية</b>
• <code>الاعدادات</code> — لوحة التحكم بالأزرار
• <code>قفل الروابط</code> / <code>فتح الروابط</code>
• <code>قفل الكل</code> / <code>فتح الكل</code>
الأقفال: {locks}
• <code>العقوبه حذف</code> | <code>تحذير</code> | <code>كتم</code>
• <code>عدد التكرار 6</code>
• <code>مده الجدد 24</code> — ساعات منع الوسائط على العضو الجديد
• <code>منع كلمه ...</code> / <code>الغاء منع ...</code> / <code>قائمه المنع</code>

👮 <b>الإدارة</b> (بالرد أو الايدي أو المعرف)
• <code>حظر</code> / <code>الغاء الحظر</code>
• <code>كتم</code> أو <code>كتم 30د</code> (د/س/ي) / <code>الغاء الكتم</code>
• <code>طرد</code>
• <code>تحذير</code> / <code>مسح التحذيرات</code> / <code>التحذيرات</code>
• <code>عدد التحذيرات 3</code>
• <code>عقوبه التحذيرات كتم</code> | <code>حظر</code> | <code>طرد</code>
• <code>تثبيت</code> / <code>الغاء التثبيت</code>
• <code>مسح</code> (بالرد) أو <code>مسح 50</code>

💬 <b>الردود</b>
• <code>اضف رد</code> / <code>حذف رد ...</code> / <code>الردود</code>

👋 <b>الترحيب والقوانين</b>
• <code>ضع ترحيب ...</code> (تقدر تستخدم {{الاسم}} و {{المجموعه}})
• <code>تفعيل الترحيب</code> / <code>تعطيل الترحيب</code>
• <code>ضع قوانين ...</code> / <code>القوانين</code>

⭐ <b>الرتب</b>
• <code>رفع مميز</code> / <code>تنزيل مميز</code> / <code>المميزين</code>
• <code>رفع ادمن</code> / <code>تنزيل ادمن</code> / <code>الادمنيه</code> (للمالك)

🛠 <b>المطور</b>
• <code>حظر عام</code> / <code>الغاء العام</code> — حظر من كل مجموعات البوت

ℹ️ <b>عام</b>
• <code>ايدي</code> — معلوماتك
• <code>تحديث</code> — تحديث قائمة المشرفين
""".format(locks="، ".join(LOCKS.values()))


# ====================== أوامر المجموعة ======================
async def c_help(update, context, arg, lvl):
    await update.effective_message.reply_text(HELP, parse_mode=HTML)


async def c_panel(update, context, arg, lvl):
    await update.effective_message.reply_text(
        PANEL_TEXT, parse_mode=HTML, reply_markup=panel(update.effective_chat.id)
    )


async def _lock_cmd(update, arg, on: bool):
    msg = update.effective_message
    chat_id = msg.chat_id
    name = norm(arg.strip())
    word = "قفل" if on else "فتح"
    icon = "🔒" if on else "🔓"
    if name == "الكل":
        for k in LOCK_ALL:
            set_lock(chat_id, k, on)
        await msg.reply_text(f"{icon} تم {word} كل الوسائط والروابط.")
        return
    key = NAME_TO_LOCK.get(name)
    if not key:
        return                                  # كلام عادي، مو أمر
    set_lock(chat_id, key, on)
    await msg.reply_text(f"{icon} تم {word} {LOCKS[key]}.")


async def c_lock(update, context, arg, lvl):
    await _lock_cmd(update, arg, True)


async def c_unlock(update, context, arg, lvl):
    await _lock_cmd(update, arg, False)


async def c_ban(update, context, arg, lvl):
    t = await need_target(update, context, arg, lvl)
    if not t:
        return
    msg = update.effective_message
    if await attempt(msg, context.bot.ban_chat_member(msg.chat_id, t[0])):
        await msg.reply_text(f"🚫 تم حظر {mention(t[0], t[1])}", parse_mode=HTML)


async def c_unban(update, context, arg, lvl):
    msg = update.effective_message
    uid, name, _ = await get_target(update, arg)
    if not uid:
        await msg.reply_text("↩️ رد على رسالته أو اكتب الايدي / المعرف.")
        return
    if await attempt(msg, context.bot.unban_chat_member(msg.chat_id, uid, only_if_banned=True)):
        await msg.reply_text(f"✅ تم إلغاء حظر {mention(uid, name)}", parse_mode=HTML)


async def c_mute(update, context, arg, lvl):
    t = await need_target(update, context, arg, lvl)
    if not t:
        return
    msg = update.effective_message
    sec = parse_duration(t[2])
    if await attempt(msg, mute(context.bot, msg.chat_id, t[0], sec)):
        await msg.reply_text(
            f"🔇 تم كتم {mention(t[0], t[1])}\nالمدة: {fmt_duration(sec)}", parse_mode=HTML
        )


async def c_unmute(update, context, arg, lvl):
    msg = update.effective_message
    uid, name, _ = await get_target(update, arg)
    if not uid:
        await msg.reply_text("↩️ رد على رسالته أو اكتب الايدي / المعرف.")
        return
    if await attempt(msg, unmute(context.bot, msg.chat_id, uid)):
        await msg.reply_text(f"🔊 تم إلغاء كتم {mention(uid, name)}", parse_mode=HTML)


async def c_kick(update, context, arg, lvl):
    t = await need_target(update, context, arg, lvl)
    if not t:
        return
    msg = update.effective_message
    if await attempt(msg, kick(context.bot, msg.chat_id, t[0])):
        await msg.reply_text(f"👢 تم طرد {mention(t[0], t[1])}", parse_mode=HTML)


async def c_warn(update, context, arg, lvl):
    t = await need_target(update, context, arg, lvl)
    if not t:
        return
    reason = t[2].strip() or "مخالفة القوانين"
    await add_warn(context, update.effective_chat.id, t[0], t[1], html.escape(reason))


async def c_clearwarns(update, context, arg, lvl):
    msg = update.effective_message
    uid, name, _ = await get_target(update, arg)
    if not uid:
        await msg.reply_text("↩️ رد على رسالته أو اكتب الايدي / المعرف.")
        return
    set_warns(msg.chat_id, uid, 0)
    await msg.reply_text(f"🧹 تم مسح تحذيرات {mention(uid, name)}", parse_mode=HTML)


async def c_warns(update, context, arg, lvl):
    msg = update.effective_message
    uid, name, _ = await get_target(update, arg)
    if not uid:
        uid, name = msg.from_user.id, msg.from_user.full_name
    limit = get_s(msg.chat_id, "warn_limit", 3)
    await msg.reply_text(
        f"⚠️ تحذيرات {mention(uid, name)}: {get_warns(msg.chat_id, uid)}/{limit}",
        parse_mode=HTML,
    )


async def c_pin(update, context, arg, lvl):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("↩️ رد على الرسالة اللي تبي تثبتها.")
        return
    if await attempt(msg, msg.reply_to_message.pin(disable_notification=True)):
        await msg.reply_text("📌 تم التثبيت.")


async def c_unpin(update, context, arg, lvl):
    msg = update.effective_message
    if await attempt(msg, context.bot.unpin_chat_message(msg.chat_id)):
        await msg.reply_text("📌 تم إلغاء التثبيت.")


async def c_purge(update, context, arg, lvl):
    msg = update.effective_message
    arg = arg.strip()
    if arg.isdigit():
        n = min(int(arg), 200)
        ids = list(range(msg.message_id - n, msg.message_id + 1))
        ok = True
        for i in range(0, len(ids), 100):
            ok = await attempt(msg, context.bot.delete_messages(msg.chat_id, ids[i:i + 100])) and ok
            if not ok:
                return
        await notice(context, msg.chat_id, f"🧹 تم مسح {n} رسالة.", 5)
    elif msg.reply_to_message and not arg:
        try:
            await msg.reply_to_message.delete()
            await msg.delete()
        except TelegramError:
            await msg.reply_text("⚠️ ما عندي صلاحية حذف الرسائل.")
    elif not arg:
        await msg.reply_text("↩️ رد على رسالة واكتب «مسح»، أو اكتب «مسح 50».")


async def c_addreply(update, context, arg, lvl):
    msg = update.effective_message
    STATE[(msg.chat_id, msg.from_user.id)] = ("trigger", None)
    await msg.reply_text("✏️ أرسل الكلمة اللي تبي البوت يرد عليها.\n(للإلغاء اكتب: الغاء)")


async def c_delreply(update, context, arg, lvl):
    msg = update.effective_message
    trig = norm(arg.strip())
    if not trig:
        await msg.reply_text("اكتب: حذف رد + الكلمة")
        return
    if replies(msg.chat_id).pop(trig, None) is None:
        await msg.reply_text("❌ ما فيه رد بهالكلمة.")
        return
    db.execute("DELETE FROM replies WHERE chat_id=? AND trig=?", (msg.chat_id, trig))
    db.commit()
    await msg.reply_text("🗑 تم حذف الرد.")


async def c_replies(update, context, arg, lvl):
    msg = update.effective_message
    r = replies(msg.chat_id)
    if not r:
        await msg.reply_text("📭 ما فيه ردود مضافة.")
        return
    lines = "\n".join(f"• <code>{html.escape(k)}</code>" for k in list(r)[:100])
    await msg.reply_text(f"💬 <b>الردود ({len(r)})</b>\n{lines}", parse_mode=HTML)


async def c_addbad(update, context, arg, lvl):
    msg = update.effective_message
    word = clean(arg.strip())
    if len(word) < 2:
        await msg.reply_text("اكتب: منع كلمه + الكلمة")
        return
    badwords(msg.chat_id).add(word)
    db.execute("INSERT OR IGNORE INTO badwords VALUES(?,?)", (msg.chat_id, word))
    db.commit()
    try:
        await msg.delete()
    except TelegramError:
        pass
    await notice(context, msg.chat_id, "🚫 تمت إضافة الكلمة لقائمة المنع.", 10)


async def c_delbad(update, context, arg, lvl):
    msg = update.effective_message
    word = clean(arg.strip())
    if word not in badwords(msg.chat_id):
        await msg.reply_text("❌ الكلمة مو موجودة في قائمة المنع.")
        return
    badwords(msg.chat_id).discard(word)
    db.execute("DELETE FROM badwords WHERE chat_id=? AND word=?", (msg.chat_id, word))
    db.commit()
    await msg.reply_text("✅ تم إلغاء منع الكلمة.")


async def c_badlist(update, context, arg, lvl):
    msg = update.effective_message
    words = badwords(msg.chat_id)
    if not words:
        await msg.reply_text("📭 قائمة المنع فاضية.")
        return
    lines = "\n".join(f"• <tg-spoiler>{html.escape(w)}</tg-spoiler>" for w in sorted(words))
    await msg.reply_text(f"🚫 <b>قائمة المنع ({len(words)})</b>\n{lines}", parse_mode=HTML)


async def c_setwelcome(update, context, arg, lvl):
    msg = update.effective_message
    if not arg.strip():
        await msg.reply_text("اكتب: ضع ترحيب + النص\nتقدر تستخدم {الاسم} و {المجموعه}")
        return
    set_s(msg.chat_id, "welcome", arg.strip())
    set_s(msg.chat_id, "welcome_on", "1")
    await msg.reply_text("✅ تم حفظ الترحيب.")


async def c_welcome_on(update, context, arg, lvl):
    set_s(update.effective_chat.id, "welcome_on", "1")
    await update.effective_message.reply_text("✅ تم تفعيل الترحيب.")


async def c_welcome_off(update, context, arg, lvl):
    set_s(update.effective_chat.id, "welcome_on", "0")
    await update.effective_message.reply_text("✅ تم تعطيل الترحيب.")


async def c_setrules(update, context, arg, lvl):
    msg = update.effective_message
    if not arg.strip():
        await msg.reply_text("اكتب: ضع قوانين + النص")
        return
    set_s(msg.chat_id, "rules", arg.strip())
    await msg.reply_text("✅ تم حفظ القوانين.")


async def c_rules(update, context, arg, lvl):
    msg = update.effective_message
    rules = get_s(msg.chat_id, "rules")
    await msg.reply_text(f"📜 القوانين:\n\n{rules}" if rules else "📭 ما تم وضع قوانين للمجموعة.")


async def c_penalty(update, context, arg, lvl):
    msg = update.effective_message
    opt = {"حذف": "delete", "تحذير": "warn", "كتم": "mute"}.get(norm(arg.strip()))
    if not opt:
        if not arg.strip():
            await msg.reply_text("اكتب: العقوبه حذف  أو  تحذير  أو  كتم")
        return
    set_s(msg.chat_id, "penalty", opt)
    await msg.reply_text(f"✅ عقوبة مخالفة الأقفال صارت: {arg.strip()}")


async def c_warn_action(update, context, arg, lvl):
    msg = update.effective_message
    opt = {"كتم": "mute", "حظر": "ban", "طرد": "kick"}.get(norm(arg.strip()))
    if not opt:
        await msg.reply_text("اكتب: عقوبه التحذيرات كتم  أو  حظر  أو  طرد")
        return
    set_s(msg.chat_id, "warn_action", opt)
    await msg.reply_text(f"✅ عقوبة الوصول لحد التحذيرات صارت: {arg.strip()}")


async def c_warn_limit(update, context, arg, lvl):
    msg = update.effective_message
    a = arg.strip()
    if not a.isdigit() or not 1 <= int(a) <= 20:
        await msg.reply_text("اكتب رقم من 1 إلى 20، مثال: عدد التحذيرات 3")
        return
    set_s(msg.chat_id, "warn_limit", int(a))
    await msg.reply_text(f"✅ حد التحذيرات صار {int(a)}.")


async def c_flood_limit(update, context, arg, lvl):
    msg = update.effective_message
    a = arg.strip()
    if not a.isdigit() or not 3 <= int(a) <= 30:
        await msg.reply_text("اكتب رقم من 3 إلى 30، مثال: عدد التكرار 6")
        return
    set_s(msg.chat_id, "flood_limit", int(a))
    await msg.reply_text(f"✅ حد التكرار صار {int(a)} رسائل خلال 5 ثواني.")


async def c_newbie_hours(update, context, arg, lvl):
    msg = update.effective_message
    a = arg.strip()
    if not a.isdigit() or not 1 <= int(a) <= 720:
        await msg.reply_text("اكتب عدد الساعات من 1 إلى 720، مثال: مده الجدد 24")
        return
    set_s(msg.chat_id, "newbie_hours", int(a))
    await msg.reply_text(f"✅ العضو الجديد ما يرسل وسائط أو روابط أول {int(a)} ساعة.")


async def c_gban(update, context, arg, lvl):
    t = await need_target(update, context, arg, lvl)
    if not t:
        return
    msg = update.effective_message
    GBANS.add(t[0])
    db.execute("INSERT OR IGNORE INTO gbans VALUES(?)", (t[0],))
    db.commit()
    try:
        await context.bot.ban_chat_member(msg.chat_id, t[0])
    except TelegramError:
        pass
    await msg.reply_text(
        f"⛔️ تم حظر {mention(t[0], t[1])} حظر عام من كل مجموعات البوت.", parse_mode=HTML
    )


async def c_ungban(update, context, arg, lvl):
    msg = update.effective_message
    uid, name, _ = await get_target(update, arg)
    if not uid:
        await msg.reply_text("↩️ رد على رسالته أو اكتب الايدي / المعرف.")
        return
    GBANS.discard(uid)
    db.execute("DELETE FROM gbans WHERE user_id=?", (uid,))
    db.commit()
    await msg.reply_text(f"✅ تم إلغاء الحظر العام عن {mention(uid, name)}", parse_mode=HTML)


async def c_refresh(update, context, arg, lvl):
    await tg_admins(context, update.effective_chat.id, force=True)
    await update.effective_message.reply_text("🔄 تم تحديث قائمة المشرفين.")


async def _set_rank(update, context, arg, lvl, rank, label):
    msg = update.effective_message
    t = await need_target(update, context, arg, lvl)
    if not t:
        return
    if rank:
        ranks(msg.chat_id)[t[0]] = rank
        db.execute("INSERT OR REPLACE INTO ranks VALUES(?,?,?)", (msg.chat_id, t[0], rank))
        text = f"⬆️ تم رفع {mention(t[0], t[1])} {label}"
    else:
        ranks(msg.chat_id).pop(t[0], None)
        db.execute("DELETE FROM ranks WHERE chat_id=? AND user_id=?", (msg.chat_id, t[0]))
        text = f"⬇️ تم تنزيل {mention(t[0], t[1])} {label}"
    db.commit()
    await msg.reply_text(text, parse_mode=HTML)


async def c_promote_admin(update, context, arg, lvl):
    await _set_rank(update, context, arg, lvl, "admin", "ادمن في البوت 👮")


async def c_demote_admin(update, context, arg, lvl):
    await _set_rank(update, context, arg, lvl, None, "من الادمنية")


async def c_promote_vip(update, context, arg, lvl):
    await _set_rank(update, context, arg, lvl, "vip", "مميز ⭐")


async def c_demote_vip(update, context, arg, lvl):
    await _set_rank(update, context, arg, lvl, None, "من المميزين")


async def _list_rank(update, rank, title):
    msg = update.effective_message
    ids = [u for u, r in ranks(msg.chat_id).items() if r == rank]
    if not ids:
        await msg.reply_text("📭 القائمة فاضية.")
        return
    lines = []
    for uid in ids:
        row = db.execute("SELECT name FROM users WHERE user_id=?", (uid,)).fetchone()
        lines.append(f"• {mention(uid, row[0] if row else str(uid))}")
    await msg.reply_text(f"<b>{title}</b>\n" + "\n".join(lines), parse_mode=HTML)


async def c_admins(update, context, arg, lvl):
    await _list_rank(update, "admin", "👮 ادمنية البوت")


async def c_vips(update, context, arg, lvl):
    await _list_rank(update, "vip", "⭐ المميزين")


async def c_id(update, context, arg, lvl):
    msg = update.effective_message
    u = msg.from_user
    target_lvl = lvl
    if msg.reply_to_message and msg.reply_to_message.from_user:
        u = msg.reply_to_message.from_user
        target_lvl = await level(context, msg.chat_id, u.id)
    row = db.execute(
        "SELECT msgs FROM members WHERE chat_id=? AND user_id=?", (msg.chat_id, u.id)
    ).fetchone()
    await msg.reply_text(
        f"👤 الاسم: {mention(u.id, u.full_name)}\n"
        f"🆔 الايدي: <code>{u.id}</code>\n"
        f"🔗 المعرف: {'@' + u.username if u.username else 'ما عنده'}\n"
        f"🎖 الرتبة: {LEVEL_NAMES[target_lvl]}\n"
        f"✉️ الرسائل: {row[0] if row else 0}\n"
        f"⚠️ التحذيرات: {get_warns(msg.chat_id, u.id)}",
        parse_mode=HTML,
    )


# (الأمر، الدالة، أقل رتبة مطلوبة)
_COMMANDS = [
    ("الاوامر", c_help, 0), ("اوامر", c_help, 0),
    ("ايدي", c_id, 0), ("id", c_id, 0),
    ("القوانين", c_rules, 0),
    ("التحذيرات", c_warns, 0),
    ("الاعدادات", c_panel, 2), ("الحمايه", c_panel, 2),
    ("قفل", c_lock, 2), ("فتح", c_unlock, 2),
    ("حظر", c_ban, 2), ("الغاء الحظر", c_unban, 2),
    ("كتم", c_mute, 2), ("الغاء الكتم", c_unmute, 2),
    ("طرد", c_kick, 2),
    ("تحذير", c_warn, 2), ("مسح التحذيرات", c_clearwarns, 2),
    ("تثبيت", c_pin, 2), ("الغاء التثبيت", c_unpin, 2),
    ("مسح", c_purge, 2),
    ("اضف رد", c_addreply, 2), ("حذف رد", c_delreply, 2), ("الردود", c_replies, 2),
    ("منع كلمه", c_addbad, 2), ("الغاء منع", c_delbad, 2), ("قائمه المنع", c_badlist, 2),
    ("ضع ترحيب", c_setwelcome, 2),
    ("تفعيل الترحيب", c_welcome_on, 2), ("تعطيل الترحيب", c_welcome_off, 2),
    ("ضع قوانين", c_setrules, 2),
    ("العقوبه", c_penalty, 2),
    ("عقوبه التحذيرات", c_warn_action, 2),
    ("عدد التحذيرات", c_warn_limit, 2),
    ("عدد التكرار", c_flood_limit, 2),
    ("تحديث", c_refresh, 2),
    ("مده الجدد", c_newbie_hours, 2),
    ("حظر عام", c_gban, 4), ("الغاء العام", c_ungban, 4),
    ("رفع مميز", c_promote_vip, 2), ("تنزيل مميز", c_demote_vip, 2),
    ("المميزين", c_vips, 2),
    ("رفع ادمن", c_promote_admin, 3), ("تنزيل ادمن", c_demote_admin, 3),
    ("الادمنيه", c_admins, 2),
]
COMMANDS = sorted(((norm(p), f, n) for p, f, n in _COMMANDS), key=lambda x: -len(x[0]))


def match_command(text: str):
    n = norm(text)
    for phrase, fn, need in COMMANDS:
        if n == phrase or n.startswith(phrase + " "):
            return fn, need, text[len(phrase):].strip()
    return None


# ====================== معالجة رسائل المجموعة ======================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = msg.from_user if msg else None
    if not msg or not chat:
        return
    edited = update.edited_message is not None
    chat_id = chat.id

    # رسائل باسم قناة / مشرف مخفي
    if msg.sender_chat:
        if msg.is_automatic_forward:            # منشور القناة المربوطة
            return
        if msg.sender_chat.id == chat_id:       # مشرف مخفي
            lvl = 2
        else:
            if locked(chat_id, "channels"):
                await punish(update, context, "القنوات")
            return
    elif not user:
        return
    else:
        lvl = await level(context, chat_id, user.id)

    track(chat, None if msg.sender_chat else user, count=not edited)

    # ---------- الحماية (للأعضاء فقط) ----------
    if lvl == 0:
        if user.id in GBANS:
            try:
                await msg.delete()
                await context.bot.ban_chat_member(chat_id, user.id)
            except TelegramError:
                pass
            return
        # الرسائل القديمة (اللي تراكمت والبوت طافي) ما تنحسب تكرار
        fresh = time.time() - msg.date.timestamp() < 60
        if locked(chat_id, "flood") and not edited and fresh and is_flooding(chat_id, msg):
            fkey = (chat_id, user.id)             # لو كررها خلال ساعة ينكتم يوم
            minutes = 1440 if time.time() - FLOOD_HIT.get(fkey, 0) < 3600 else 10
            FLOOD_HIT[fkey] = time.time()
            try:
                await mute(context.bot, chat_id, user.id, minutes * 60)
                await msg.delete()
            except TelegramError:
                pass
            await notice(
                context, chat_id,
                f"🔇 {mention(user.id, user.full_name)} انكتم ({fmt_duration(minutes * 60)}) بسبب التكرار.", 30,
            )
            return
        reason = violation(msg, chat_id, edited, user.id)
        if reason:
            await punish(update, context, reason)
            return

    text = msg.text
    if not text or edited:
        return
    text = text.strip()

    # ---------- خطوات إضافة رد ----------
    skey = (chat_id, user.id)
    if skey in STATE and not msg.sender_chat:
        stage, trig = STATE[skey]
        if norm(text) == "الغاء":
            STATE.pop(skey, None)
            await msg.reply_text("تم الإلغاء.")
        elif stage == "trigger":
            STATE[skey] = ("response", norm(text))
            await msg.reply_text("👍 الحين أرسل الرد.\nتقدر تستخدم {الاسم} داخل الرد.")
        else:
            STATE.pop(skey, None)
            replies(chat_id)[trig] = text
            db.execute("INSERT OR REPLACE INTO replies VALUES(?,?,?)", (chat_id, trig, text))
            db.commit()
            await msg.reply_text("✅ تم حفظ الرد.")
        return

    # ---------- الأوامر ----------
    m = match_command(text)
    if m and lvl >= m[1]:
        try:
            await m[0](update, context, m[2], lvl)
        except TelegramError as e:
            log.warning("command error: %s", e)
        return

    # ---------- الردود التلقائية ----------
    resp = replies(chat_id).get(norm(text))
    if resp:
        name = user.first_name if user else ""
        await msg.reply_text(resp.replace("{الاسم}", name))


# ====================== دخول وخروج الأعضاء ======================
def is_member(cm) -> bool:
    return cm.status in ("member", "administrator", "creator") or (
        cm.status == "restricted" and bool(getattr(cm, "is_member", False))
    )


def save_pending(chat_id: int, uid: int, mid: int, ans: int, ts: float) -> None:
    PENDING[(chat_id, uid)] = {"mid": mid, "ans": ans, "tries": 0, "ts": ts}
    db.execute("INSERT OR REPLACE INTO pending VALUES(?,?,?,?,?)", (chat_id, uid, mid, ans, ts))
    db.commit()


def drop_pending(chat_id: int, uid: int):
    p = PENDING.pop((chat_id, uid), None)
    db.execute("DELETE FROM pending WHERE chat_id=? AND user_id=?", (chat_id, uid))
    db.commit()
    return p


async def captcha_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, uid, ts = context.job.data
    p = PENDING.get((chat_id, uid))
    if not p or p["ts"] != ts:
        return
    drop_pending(chat_id, uid)
    try:
        await kick(context.bot, chat_id, uid)
    except TelegramError:
        pass
    try:
        await context.bot.delete_message(chat_id, p["mid"])
    except TelegramError:
        pass


async def start_captcha(context, chat, m) -> None:
    """يكتم العضو الجديد ويعطيه سؤال حسابي، لازم يجاوب صح عشان ينفتح له الكلام."""
    await mute(context.bot, chat.id, m.id)
    old = drop_pending(chat.id, m.id)
    if old:
        try:
            await context.bot.delete_message(chat.id, old["mid"])
        except TelegramError:
            pass
    a, b = random.randint(1, 9), random.randint(1, 9)
    ans = a + b
    opts = {ans}
    while len(opts) < 4:
        opts.add(random.randint(2, 18))
    opts = list(opts)
    random.shuffle(opts)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(str(o), callback_data=f"cap:{m.id}:{o}") for o in opts]]
    )
    sent = await context.bot.send_message(
        chat.id,
        f"🛡 أهلاً {mention(m.id, m.first_name)}\n"
        "للتأكد إنك مو روبوت، اختر الجواب الصحيح خلال دقيقتين وإلا راح تنطرد:\n\n"
        f"<b>{a} + {b} = ؟</b>",
        parse_mode=HTML, reply_markup=kb,
    )
    ts = time.time()
    save_pending(chat.id, m.id, sent.message_id, ans, ts)
    if context.job_queue:
        context.job_queue.run_once(captcha_timeout, CAPTCHA_SECONDS, data=(chat.id, m.id, ts))


async def raid_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    set_lock(chat_id, "joins", False)
    await notice(context, chat_id, "✅ انتهى وضع الطوارئ وتم فتح الدخول.", 0)


async def send_welcome(context, chat, user) -> None:
    if get_s(chat.id, "welcome_on", "1") != "1":
        return
    text = get_s(chat.id, "welcome", "أهلاً {الاسم} نورت {المجموعه} 🌹")
    text = html.escape(text).replace("{الاسم}", mention(user.id, user.first_name))
    text = text.replace("{المجموعه}", html.escape(chat.title or ""))
    await notice(context, chat.id, text, 0)


async def process_join(context, chat, m, adder) -> None:
    """كل فحوصات الدخول لعضو واحد (تُستدعى من رسالة الدخول ومن تحديث العضوية)."""
    chat_id = chat.id
    key = (chat_id, m.id)
    now = time.time()
    if m.id == context.bot.id or now - SEEN_JOIN.get(key, 0) < 15:
        return
    SEEN_JOIN[key] = now
    adder_lvl = await level(context, chat_id, adder.id) if adder else 0
    by_admin = bool(adder and adder.id != m.id and adder_lvl >= 2)
    try:
        if m.is_bot:
            if locked(chat_id, "bots") and adder_lvl < 2:
                await context.bot.ban_chat_member(chat_id, m.id)
                await notice(context, chat_id, "🤖 ممنوع إضافة البوتات، تم طرد البوت.")
            return

        if m.id in GBANS:
            await context.bot.ban_chat_member(chat_id, m.id)
            await notice(context, chat_id, "⛔️ تم حظر عضو موجود في قائمة الحظر العام.")
            return

        JOINED[key] = now
        db.execute("INSERT OR REPLACE INTO joined VALUES(?,?,?)", (chat_id, m.id, now))
        db.commit()

        if by_admin:                                   # مضاف من مشرف
            await send_welcome(context, chat, m)
            return

        if locked(chat_id, "joins"):
            await kick(context.bot, chat_id, m.id)
            return

        name = m.full_name or ""
        if locked(chat_id, "names") and (
            LINK_RE.search(name) or USERNAME_RE.search(name) or len(name) > 60
        ):
            await kick(context.bot, chat_id, m.id)
            await notice(context, chat_id, "🚫 تم طرد حساب اسمه فيه رابط أو إعلان.")
            return

        if locked(chat_id, "raid"):
            dq = JOINS[chat_id]
            dq.append((now, m.id))
            recent = [u for t, u in dq if now - t <= 20]
            if len(recent) >= 8:
                dq.clear()
                set_lock(chat_id, "joins", True)
                for u in recent:                       # طرد كل اللي دخلوا مع الهجوم
                    try:
                        await kick(context.bot, chat_id, u)
                        pend = drop_pending(chat_id, u)
                        if pend:
                            await context.bot.delete_message(chat_id, pend["mid"])
                    except TelegramError:
                        pass
                await notice(
                    context, chat_id,
                    "🚨 <b>تم رصد هجوم دخول!</b>\n"
                    f"تم طرد {len(recent)} حساب وقفل الدخول 10 دقايق تلقائياً.", 0,
                )
                if context.job_queue:
                    context.job_queue.run_once(raid_end, 600, data=chat_id)
                return

        if locked(chat_id, "captcha"):
            await start_captcha(context, chat, m)
        else:
            await send_welcome(context, chat, m)
    except TelegramError as e:
        log.warning("join handling error: %s", e)


async def on_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    track(chat, None, False)
    for m in msg.new_chat_members:
        await process_join(context, chat, m, msg.from_user)
    if locked(chat.id, "tgservice"):
        try:
            await msg.delete()
        except TelegramError:
            pass


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يلقط الدخول حتى لو رسائل الدخول مخفية، ويحدّث المشرفين فوراً."""
    cm = update.chat_member
    if not cm or cm.chat.type not in ("group", "supergroup"):
        return
    old, new = cm.old_chat_member, cm.new_chat_member
    admin = ("administrator", "creator")
    if (old.status in admin) != (new.status in admin):
        ADMINS.pop(cm.chat.id, None)                   # رفع/تنزيل مشرف
    if not is_member(old) and is_member(new):
        await process_join(context, cm.chat, new.user, cm.from_user)
    elif is_member(old) and not is_member(new):
        p = drop_pending(cm.chat.id, new.user.id)      # طلع قبل ما يتحقق
        if p:
            try:
                await context.bot.delete_message(cm.chat.id, p["mid"])
            except TelegramError:
                pass


async def on_left(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if locked(msg.chat_id, "tgservice"):
        try:
            await msg.delete()
        except TelegramError:
            pass


# ====================== الأزرار ======================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    chat_id = q.message.chat.id
    uid = q.from_user.id

    if data.startswith("cap:"):
        parts = data.split(":")
        if uid != int(parts[1]):
            await q.answer("السؤال هذا مو لك 🙂", show_alert=True)
            return
        p = PENDING.get((chat_id, uid))
        if not p or len(parts) < 3:
            await q.answer("انتهى وقت التحقق.", show_alert=True)
            try:
                await q.message.delete()
            except TelegramError:
                pass
            return
        if int(parts[2]) == p["ans"]:
            drop_pending(chat_id, uid)
            try:
                await unmute(context.bot, chat_id, uid)
                await q.message.delete()
            except TelegramError:
                pass
            await q.answer("تم التحقق ✅")
            await send_welcome(context, q.message.chat, q.from_user)
            return
        p["tries"] += 1
        if p["tries"] < 2:
            await q.answer("❌ جواب غلط، باقي لك محاولة وحدة.", show_alert=True)
            return
        drop_pending(chat_id, uid)
        await q.answer("❌ فشلت في التحقق.", show_alert=True)
        try:
            until = datetime.now(timezone.utc) + timedelta(minutes=10)
            await context.bot.ban_chat_member(chat_id, uid, until_date=until)
            await q.message.delete()
        except TelegramError:
            pass
        return

    if await level(context, chat_id, uid) < 2:
        await q.answer("اللوحة للمشرفين فقط 🚫", show_alert=True)
        return

    if data == "close":
        await q.answer()
        try:
            await q.message.delete()
        except TelegramError:
            pass
    elif data.startswith("lk:"):
        key = data[3:]
        if key in LOCKS:
            on = not locked(chat_id, key)
            set_lock(chat_id, key, on)
            await q.answer(f"{'🔒 تم قفل' if on else '🔓 تم فتح'} {LOCKS[key]}")
            try:
                await q.edit_message_reply_markup(reply_markup=panel(chat_id))
            except TelegramError:
                pass


# ====================== إضافة/إزالة البوت من مجموعة ======================
async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cm = update.my_chat_member
    chat = cm.chat
    if chat.type not in ("group", "supergroup"):
        return
    new = cm.new_chat_member.status
    if new in ("left", "kicked"):
        db.execute("DELETE FROM chats WHERE chat_id=?", (chat.id,))
        db.commit()
        return
    track(chat, None, False)
    if cm.old_chat_member.status in ("left", "kicked"):
        await notice(
            context, chat.id,
            "👋 أهلاً، أنا بوت الحماية.\n"
            "ارفعني <b>مشرف</b> مع صلاحيات (حذف الرسائل، حظر الأعضاء، التثبيت) عشان أشتغل.\n"
            "بعدها اكتب <code>الاعدادات</code> أو <code>الاوامر</code>.", 0,
        )


# ====================== الخاص ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    me = context.bot.username
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ أضفني لمجموعتك", url=f"https://t.me/{me}?startgroup=true")]]
    )
    await update.effective_message.reply_text(
        "👋 أهلاً بك في بوت الحماية.\n\n"
        "🛡 أقفال للروابط والوسائط والتوجيه\n"
        "🚨 حماية من التكرار وهجمات الدخول\n"
        "🤖 تحقق بسؤال لكل عضو جديد\n"
        "💬 ردود تلقائية وترحيب\n"
        "👮 حظر، كتم، طرد، تحذيرات ورتب\n\n"
        "أضفني لمجموعتك وارفعني مشرف، ثم اكتب: الاوامر",
        reply_markup=kb,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP, parse_mode=HTML)


async def on_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    text = (msg.text or "").strip()
    n = norm(text)
    if n in ("الاوامر", "اوامر"):
        await msg.reply_text(HELP, parse_mode=HTML)
        return
    if not OWNER_ID or msg.from_user.id != OWNER_ID:
        return
    if n == "الاحصائيات":
        chats = db.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        await msg.reply_text(f"📊 المجموعات: {chats}\n👥 الأعضاء المسجلين: {users}")
    elif n.startswith("اذاعه "):
        body = text[len("اذاعه "):].strip()
        ok = 0
        for (cid,) in db.execute("SELECT chat_id FROM chats").fetchall():
            try:
                await context.bot.send_message(cid, body)
                ok += 1
            except TelegramError:
                pass
        await msg.reply_text(f"📢 تم الإرسال إلى {ok} مجموعة.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("خطأ غير متوقع", exc_info=context.error)


# ====================== التشغيل ======================
async def post_init(app: Application) -> None:
    """يرجّع التحققات المعلقة بعد إعادة التشغيل عشان ما يبقى أحد مكتوم."""
    now = time.time()
    for chat_id, uid, mid, ans, ts in db.execute("SELECT * FROM pending").fetchall():
        PENDING[(chat_id, uid)] = {"mid": mid, "ans": ans, "tries": 0, "ts": ts}
        if app.job_queue:
            app.job_queue.run_once(
                captcha_timeout, max(5, CAPTCHA_SECONDS - (now - ts)), data=(chat_id, uid, ts)
            )


    if app.job_queue:
        app.job_queue.run_repeating(_save_job, 300, first=300)
        if RUN_SECONDS:
            app.job_queue.run_once(_stop_job, RUN_SECONDS)


async def _save_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    save_data()


async def _stop_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    log.info("انتهت مدة التشغيل، حفظ وإغلاق.")
    context.application.stop_running()


async def post_shutdown(app: Application) -> None:
    save_data()


def build() -> Application:
    app = (
        Application.builder().token(TOKEN)
        .post_init(post_init).post_shutdown(post_shutdown).build()
    )
    groups = filters.ChatType.GROUPS
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(groups & filters.StatusUpdate.NEW_CHAT_MEMBERS, on_join))
    app.add_handler(MessageHandler(groups & filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left))
    app.add_handler(MessageHandler(groups & ~filters.StatusUpdate.ALL, on_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, on_private))
    app.add_error_handler(on_error)
    return app


if __name__ == "__main__":
    if ":" not in TOKEN:
        raise SystemExit("حط توكن البوت في المتغير BOT_TOKEN أو داخل الملف أول.")
    log.info("البوت اشتغل ✅")
    build().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=not RUN_SECONDS)
