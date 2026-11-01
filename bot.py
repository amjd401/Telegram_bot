"""
بوت حماية قروبات تلقرام
المطور: @amjd_134

- لازم يتفعل بأمر "تفعيل" بعد ما ينضاف للقروب
- رتب: المطور / المالك / المدير / الادمن / المميز / العضو
- اوامر عربية (حظر، كتم، منع...) واوامر جاهزة تبدأ بـ /
- مخالفات: 3 تحذيرات ثم تقييد يوم ثم اي مخالفة = حظر
- منع كلمات، اقفال (روابط، صور، ملصقات...)، تحقق للاعضاء الجدد
- ردود تلقائية وردود مخصصة، الوقت والتاريخ هجري وميلادي
- البيانات تنحفظ في data.json وترتفع لـ GitHub تلقائيا
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, MessageEntityType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("guard")

TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID") or 5095978872)  # ايدي المطور
GIT_SAVE = os.environ.get("GIT_SAVE") == "1"
BASE = Path(__file__).parent
DATA_FILE = BASE / "data.json"
WORDS_FILE = BASE / "words.txt"
TZ = ZoneInfo("Asia/Riyadh")

# ======================= الاعدادات =======================
MAX_WARNS = 3              # عدد التحذيرات قبل التقييد
RESTRICT_HOURS = 24        # مدة التقييد
FLOOD_LIMIT = 6            # عدد الرسائل
FLOOD_WINDOW = 8           # خلال كم ثانية
CAPTCHA_SECONDS = 180      # مهلة التحقق
NSFW_THRESHOLD = 0.45
NOTICE_DELETE_AFTER = 120  # رسائل التحذير تنحذف بعد دقيقتين

DEVELOPER = "@amjd_134"
DEVELOPER_URL = "https://t.me/amjd_134"
DEV_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("المطور", url=DEVELOPER_URL)]])

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.(me|dog)/|\b[\w-]+\.(com|net|org|me|io|xyz|link|ly|site|online|top|sa|app)\b)",
    re.IGNORECASE,
)
USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,}")

# ======================= الرتب =======================
DEV, OWNER, MANAGER, ADMIN, VIP, MEMBER = 5, 4, 3, 2, 1, 0
RANK_NAMES = {DEV: "المطور", OWNER: "المالك", MANAGER: "المدير",
              ADMIN: "الادمن", VIP: "المميز", MEMBER: "عضو"}
RANK_KEYS = {"manager": MANAGER, "admin": ADMIN, "vip": VIP}
KEY_OF = {v: k for k, v in RANK_KEYS.items()}

# ======================= الاقفال والمميزات =======================
# المفتاح: (الاسم، القيمة الافتراضية)
LOCKS = {
    "links": ("الروابط", True),
    "forward": ("التوجيه", True),
    "usernames": ("المعرفات", False),
    "photos": ("الصور", False),
    "videos": ("الفيديو", False),
    "stickers": ("الملصقات", False),
    "gifs": ("المتحركات", False),
    "files": ("الملفات", False),
    "voice": ("الصوتيات", False),
    "flood": ("التكرار", True),
    "bots": ("البوتات", True),
    "nsfw": ("الاباحي", True),
}
LOCK_ALIASES = {
    "الروابط": "links", "روابط": "links", "الرابط": "links",
    "التوجيه": "forward", "توجيه": "forward",
    "المعرفات": "usernames", "اليوزرات": "usernames", "المعرف": "usernames",
    "الصور": "photos", "صور": "photos",
    "الفيديو": "videos", "الفيديوهات": "videos", "فيديو": "videos",
    "الملصقات": "stickers", "ملصقات": "stickers", "الستيكرات": "stickers",
    "المتحركات": "gifs", "المتحركه": "gifs", "الجيف": "gifs",
    "الملفات": "files", "ملفات": "files",
    "الصوتيات": "voice", "الصوت": "voice", "البصمات": "voice",
    "التكرار": "flood", "تكرار": "flood", "السبام": "flood",
    "البوتات": "bots", "بوتات": "bots",
    "الاباحي": "nsfw", "الاباحيه": "nsfw",
}
FEATURES = {
    "captcha": ("التحقق", True),
    "welcome": ("الترحيب", True),
    "replies": ("الردود", True),
    "warns": ("الانذارات", True),
}
FEATURE_ALIASES = {
    "التحقق": "captcha", "تحقق": "captcha",
    "الترحيب": "welcome", "ترحيب": "welcome",
    "الردود": "replies", "ردود": "replies", "الرد": "replies",
    "الانذارات": "warns", "الانذار": "warns", "انذار": "warns",
    "التحذيرات": "warns", "التحذير": "warns", "المخالفات": "warns",
}

# ======================= الردود التلقائية =======================
GREETINGS = [
    ("السلام عليكم", "وعليكم السلام ورحمة الله وبركاته"),
    ("سلام عليكم", "وعليكم السلام ورحمة الله وبركاته"),
    ("صباح الخير", "صباح النور والسرور"),
    ("مساء الخير", "مساء النور والورد"),
    ("هلا", "هلا والله"),
    ("مرحبا", "مراحب"),
    ("شكرا", "العفو"),
    ("باي", "في امان الله"),
    ("تصبحون علي خير", "وانت من اهل الخير"),
]

DAYS = ["الاثنين", "الثلاثاء", "الاربعاء", "الخميس", "الجمعة", "السبت", "الاحد"]
G_MONTHS = ["يناير", "فبراير", "مارس", "ابريل", "مايو", "يونيو",
            "يوليو", "اغسطس", "سبتمبر", "اكتوبر", "نوفمبر", "ديسمبر"]
H_MONTHS = ["محرم", "صفر", "ربيع الاول", "ربيع الاخر", "جمادى الاولى", "جمادى الاخرة",
            "رجب", "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة"]

# ======================= حفظ البيانات =======================
def _load_data() -> dict:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"chats": {}}


DATA = _load_data()
_dirty = False


def chat_data(chat_id) -> dict:
    c = DATA.setdefault("chats", {}).setdefault(str(chat_id), {})
    c.setdefault("active", False)
    c.setdefault("ranks", {})
    c.setdefault("replies", {})
    c.setdefault("words", [])
    c.setdefault("unwords", [])
    c.setdefault("warns", {})
    locks = c.setdefault("locks", {})
    for k, (_, default) in LOCKS.items():
        locks.setdefault(k, default)
    feats = c.setdefault("features", {})
    for k, (_, default) in FEATURES.items():
        feats.setdefault(k, default)
    return c


def mark_dirty():
    global _dirty
    _dirty = True


def write_data():
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(DATA, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA_FILE)


def _git(*args):
    return subprocess.run(["git", *args], cwd=BASE, capture_output=True, text=True)


def git_push():
    _git("add", "data.json")
    if _git("commit", "-m", "bot data update [skip ci]").returncode != 0:
        return
    if _git("push").returncode != 0:
        _git("pull", "--rebase", "-X", "theirs")
        r = _git("push")
        if r.returncode != 0:
            log.warning("git push failed: %s", r.stderr)


async def saver_loop():
    global _dirty
    need_push, last_push = False, time.time()
    while True:
        await asyncio.sleep(20)
        try:
            if _dirty:
                _dirty = False
                write_data()
                need_push = True
            if GIT_SAVE and need_push and time.time() - last_push > 300:
                await asyncio.to_thread(git_push)
                need_push, last_push = False, time.time()
        except Exception as e:
            log.warning("saver error: %s", e)


# ======================= تطبيع النص =======================
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s", "!": "i"})
_PREFIXES = ("وال", "بال", "فال", "لل", "ال", "يا", "و", "ف", "ب")


def simple_norm(text: str) -> str:
    """تطبيع خفيف للاوامر والردود."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _INVISIBLE.sub("", text)
    text = _DIACRITICS.sub("", text)
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """تطبيع قوي لكشف الكلمات الممنوعة."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _INVISIBLE.sub("", text)
    text = _DIACRITICS.sub("", text)
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    text = text.translate(_LEET)
    text = re.sub(r"([\u0600-\u06FF])\1+", r"\1", text)
    text = re.sub(r"([a-z])\1{2,}", r"\1\1", text)
    text = re.sub(r"(?<=\b\w)[\s.\-_*/\\|,]+(?=\w\b)", "", text)
    return text


def _collapse(s: str) -> str:
    return re.sub(r"([a-z])\1+", r"\1", s)


def _load_global_words() -> set[str]:
    words = set()
    if WORDS_FILE.exists():
        for line in WORDS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.add(normalize(line))
    return words


GLOBAL_WORDS = _load_global_words()


def words_for(chat_id) -> set[str]:
    cd = chat_data(chat_id)
    return (GLOBAL_WORDS | {normalize(w) for w in cd["words"]}) - {normalize(w) for w in cd["unwords"]}


def find_bad_word(text: str | None, bad: set[str]) -> str | None:
    if not text:
        return None
    norm = normalize(text)
    # عبارات من اكثر من كلمة
    for w in bad:
        if " " in w and w in norm:
            return w
    tokens = re.findall(r"\w+", norm)
    collapsed_bad = {_collapse(w): w for w in bad if len(_collapse(w)) >= 3}
    for t in tokens:
        candidates = {t}
        for p in _PREFIXES:
            if t.startswith(p) and len(t) - len(p) >= 2:
                candidates.add(t[len(p):])
        for c in candidates:
            if c in bad:
                return c
            if _collapse(c) in collapsed_bad:
                return collapsed_bad[_collapse(c)]
            for w in bad:
                if len(w) >= 4 and " " not in w and w in c:
                    return w
    return None


# ======================= فحص الصور =======================
try:
    from nudenet import NudeDetector
    _detector = NudeDetector()
    log.info("NudeNet loaded")
except Exception as e:
    _detector = None
    log.warning("NudeNet not available: %s", e)

NSFW_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "FEMALE_BREAST_EXPOSED",
    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
}


def _scan_image(path: str) -> bool:
    try:
        return any(r["class"] in NSFW_CLASSES and r["score"] >= NSFW_THRESHOLD
                   for r in _detector.detect(path))
    except Exception as e:
        log.warning("scan failed: %s", e)
        return False


def media_file_id(msg) -> str | None:
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.sticker:
        s = msg.sticker
        if not s.is_animated and not s.is_video:
            return s.file_id
        return s.thumbnail.file_id if s.thumbnail else None
    for media in (msg.video, msg.animation, msg.video_note):
        if media and media.thumbnail:
            return media.thumbnail.file_id
    if msg.document:
        if (msg.document.mime_type or "").startswith("image/"):
            return msg.document.file_id
        if msg.document.thumbnail:
            return msg.document.thumbnail.file_id
    return None


async def is_nsfw(msg, context) -> bool:
    if not _detector:
        return False
    file_id = media_file_id(msg)
    if not file_id:
        return False
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "img")
            f = await context.bot.get_file(file_id)
            await f.download_to_drive(path)
            return await asyncio.to_thread(_scan_image, path)
    except Exception as e:
        log.warning("nsfw check failed: %s", e)
        return False


# ======================= الرتب =======================
_tg_cache: dict[int, tuple[float, dict]] = {}


async def tg_statuses(chat_id, context) -> dict:
    cached = _tg_cache.get(chat_id)
    if cached and time.time() - cached[0] < 120:
        return cached[1]
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        m = {a.user.id: a.status for a in admins}
    except Exception as e:
        log.warning("get admins failed: %s", e)
        m = cached[1] if cached else {}
    _tg_cache[chat_id] = (time.time(), m)
    return m


async def get_rank(chat_id, user_id, context) -> int:
    if OWNER_ID and user_id == OWNER_ID:
        return DEV
    status = (await tg_statuses(chat_id, context)).get(user_id)
    if status == ChatMemberStatus.OWNER:
        return OWNER
    rank = RANK_KEYS.get(chat_data(chat_id)["ranks"].get(str(user_id)), MEMBER)
    if status == ChatMemberStatus.ADMINISTRATOR:
        rank = max(rank, ADMIN)
    return rank


async def sender_rank(update: Update, context) -> int:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if msg and msg.sender_chat and msg.sender_chat.id == chat.id:
        return MANAGER  # مشرف مخفي
    if not user:
        return MEMBER
    return await get_rank(chat.id, user.id, context)


# ======================= ادوات =======================
async def send_temp(chat, text, seconds=NOTICE_DELETE_AFTER, **kw):
    try:
        m = await chat.send_message(text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        log.warning("send failed: %s", e)
        return None
    asyncio.get_running_loop().create_task(_delete_later(m, seconds))
    return m


async def _delete_later(msg, seconds):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass


async def mute(context, chat_id, user_id, minutes=None):
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes else None
    await context.bot.restrict_chat_member(
        chat_id, user_id, ChatPermissions.no_permissions(), until_date=until
    )


async def unmute(context, chat_id, user_id):
    chat = await context.bot.get_chat(chat_id)
    await context.bot.restrict_chat_member(
        chat_id, user_id, chat.permissions or ChatPermissions.all_permissions()
    )


SUPERGROUP_HELP = (
    "القروب هذا قروب عادي، وتلقرام ما يسمح بالكتم والتقييد والتحقق الا في السوبر قروب\n\n"
    "الحل سهل:\n"
    "اعدادات القروب ثم تعديل ثم (سجل المحادثة للاعضاء الجدد) وخله <b>ظاهر</b>\n"
    "القروب يتحول سوبر قروب تلقائيا، وبعدها اكتب <code>تفعيل</code> مرة ثانية"
)


def explain_error(e: Exception) -> str:
    m = str(e).lower()
    if "supergroup" in m:
        return SUPERGROUP_HELP
    if "user is an administrator" in m or "user_admin_invalid" in m or "chat owner" in m:
        return "ما اقدر اسويها على مشرف بالقروب، لازم تنزله من الاشراف اول"
    if "not enough rights" in m or "admin_required" in m or "rights" in m:
        return ("ما عندي الصلاحية\n"
                "ادخل اعدادات القروب ثم المشرفين ثم البوت، وفعل:\n"
                "- حذف الرسائل\n- حظر المستخدمين")
    if "participant" in m or "user not found" in m:
        return "الشخص مو موجود بالقروب"
    return "ما قدرت انفذ الامر\nاكتب <code>فحص</code> عشان اشوف وش المشكلة"


def mention(user) -> str:
    return f'<a href="tg://user?id={user.id}">{escape(user.full_name or str(user.id))}</a>'


def time_text() -> str:
    n = datetime.now(TZ)
    h = n.hour % 12 or 12
    ampm = "صباحا" if n.hour < 12 else "مساء"
    return f"الساعة الحين: <b>{h}:{n.minute:02d} {ampm}</b>\nبتوقيت مكة المكرمة"


def date_text() -> str:
    n = datetime.now(TZ)
    text = (f"اليوم: <b>{DAYS[n.weekday()]}</b>\n"
            f"الميلادي: <b>{n.day} {G_MONTHS[n.month - 1]} {n.year}م</b>")
    try:
        try:
            from hijridate import Gregorian
        except ImportError:
            from hijri_converter import Gregorian
        h = Gregorian(n.year, n.month, n.day).to_hijri()
        text += f"\nالهجري: <b>{h.day} {H_MONTHS[h.month - 1]} {h.year}هـ</b>"
    except Exception as e:
        log.warning("hijri failed: %s", e)
    return text


# ======================= تتبع الاعضاء (لامر كشف) =======================
def track_user(user, chat_id=None, count_msg=False, joined=False):
    """يسجل اسم ويوزر العضو، ولو تغيروا يحفظ القديم ووقت التغيير."""
    if not user or user.is_bot:
        return
    now = int(time.time())
    users = DATA.setdefault("users", {})
    u = users.get(str(user.id))
    name, username = user.full_name or "", user.username or ""
    if u is None:
        users[str(user.id)] = {"name": name, "username": username, "first_seen": now,
                               "name_changed": None, "user_changed": None,
                               "old_names": [], "old_usernames": []}
        mark_dirty()
    else:
        if u["name"] != name:
            u["old_names"] = ([{"name": u["name"], "until": now}] + u.get("old_names", []))[:5]
            u["name"], u["name_changed"] = name, now
            mark_dirty()
        if u["username"] != username:
            u["old_usernames"] = ([{"username": u["username"], "until": now}] + u.get("old_usernames", []))[:5]
            u["username"], u["user_changed"] = username, now
            mark_dirty()
    if chat_id is not None and (count_msg or joined):
        st = chat_data(chat_id).setdefault("stats", {}).setdefault(str(user.id), {"msgs": 0})
        if count_msg:
            st["msgs"] = st.get("msgs", 0) + 1
            st["last_msg"] = now
        if joined:
            st["joined"] = now
        mark_dirty()


def find_user_id(arg: str) -> int | None:
    arg = arg.strip()
    if arg.lstrip("-").isdigit():
        return int(arg)
    uname = arg.lstrip("@").lower()
    if not uname:
        return None
    for uid, u in DATA.get("users", {}).items():
        if (u.get("username") or "").lower() == uname:
            return int(uid)
    return None


def fmt_time(ts) -> str:
    if not ts:
        return "غير معروف"
    d = datetime.fromtimestamp(ts, TZ)
    h = d.hour % 12 or 12
    ampm = "ص" if d.hour < 12 else "م"
    diff = int(time.time() - ts)
    if diff < 3600:
        ago = f"قبل {max(diff // 60, 1)} دقيقة"
    elif diff < 86400:
        ago = f"قبل {diff // 3600} ساعة"
    elif diff < 86400 * 30:
        ago = f"قبل {diff // 86400} يوم"
    elif diff < 86400 * 365:
        ago = f"قبل {diff // (86400 * 30)} شهر"
    else:
        ago = f"قبل {diff // (86400 * 365)} سنة"
    return f"{d.day} {G_MONTHS[d.month - 1]} {d.year} الساعة {h}:{d.minute:02d} {ampm} ({ago})"


# ======================= نظام المخالفات =======================
async def violation(context, chat, user, reason: str):
    cd = chat_data(chat.id)
    who = mention(user)
    if not cd["features"]["warns"]:
        await send_temp(chat, f"تم حذف رسالة {who}\nالسبب: {reason}")
        return
    w = cd["warns"].setdefault(str(user.id), {"count": 0, "restricted": False})
    try:
        if w["restricted"]:
            await context.bot.ban_chat_member(chat.id, user.id)
            cd["warns"].pop(str(user.id), None)
            text = (f"تم حظر {who} نهائيا\n"
                    f"المخالفة: {reason}\n"
                    f"السبب: خالف بعد ما تقيد")
        else:
            w["count"] += 1
            if w["count"] >= MAX_WARNS:
                await mute(context, chat.id, user.id, RESTRICT_HOURS * 60)
                w["count"], w["restricted"] = 0, True
                text = (f"تم تقييد {who} لمدة يوم كامل\n"
                        f"آخر مخالفة: {reason}\n"
                        f"السبب: وصل {MAX_WARNS} مخالفات\n\n"
                        f"تنبيه: اي مخالفة بعد كذا حظر نهائي")
            else:
                left = MAX_WARNS - w["count"]
                text = (f"تحذير {w['count']} من {MAX_WARNS}\n"
                        f"العضو: {who}\n"
                        f"المخالفة: {reason}\n"
                        f"تم حذف رسالته، باقي له {left} ويتقيد يوم كامل")
        mark_dirty()
        await send_temp(chat, text)
    except Exception as e:
        log.warning("violation failed: %s", e)
        mark_dirty()
        await send_temp(chat, f"تم حذف رسالة {who}\nالسبب: {reason}\n\n" + explain_error(e))


recent_msgs: dict[tuple[int, int], deque] = defaultdict(deque)


async def check_violation(update: Update, context, rank: int) -> str | None:
    """يرجع سبب المخالفة او None."""
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    cd = chat_data(chat.id)
    locks = cd["locks"]
    text = " ".join(filter(None, [msg.text, msg.caption]))
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    bad = words_for(chat.id)

    if find_bad_word(text, bad):
        return "كلمة ممنوعة"
    if msg.poll and find_bad_word(" ".join([msg.poll.question] + [o.text for o in msg.poll.options]), bad):
        return "كلمة ممنوعة"
    if rank >= ADMIN:
        return None

    if rank < VIP:
        if locks["links"] and (
            LINK_RE.search(text)
            or any(e.type in (MessageEntityType.URL, MessageEntityType.TEXT_LINK) for e in entities)
            or msg.reply_markup is not None
        ):
            return "ارسال روابط"
        if locks["forward"] and (msg.forward_origin or msg.via_bot or msg.story):
            return "اعادة توجيه"
        if locks["usernames"] and (
            USERNAME_RE.search(text) or any(e.type == MessageEntityType.MENTION for e in entities)
        ):
            return "ارسال معرفات"
        if locks["photos"] and msg.photo:
            return "ارسال صور والصور مقفلة"
        if locks["videos"] and (msg.video or msg.video_note):
            return "ارسال فيديو والفيديو مقفل"
        if locks["stickers"] and msg.sticker:
            return "ارسال ملصقات والملصقات مقفلة"
        if locks["gifs"] and msg.animation:
            return "ارسال متحركات والمتحركات مقفلة"
        if locks["files"] and msg.document and not msg.animation:
            return "ارسال ملفات والملفات مقفلة"
        if locks["voice"] and (msg.voice or msg.audio):
            return "ارسال صوتيات والصوتيات مقفلة"

    if locks["nsfw"] and await is_nsfw(msg, context):
        return "محتوى اباحي"

    if locks["flood"] and rank < VIP and not update.edited_message:
        key, now = (chat.id, user.id), time.time()
        q = recent_msgs[key]
        q.append(now)
        while q and now - q[0] > FLOOD_WINDOW:
            q.popleft()
        if len(q) > FLOOD_LIMIT:
            q.clear()
            return "تكرار الرسائل"
    return None


# ======================= قوائم الاوامر =======================
MEMBER_HELP = (
    "<b>اوامر الاعضاء</b> (يكتبونها بالقروب)\n\n"
    "- <code>الوقت</code> : الساعة الحين\n"
    "- <code>التاريخ</code> : التاريخ هجري وميلادي\n"
    "- <code>ايدي</code> : معلوماتك\n"
    "- <code>رتبتي</code> : رتبتك\n"
    "- <code>رتبته</code> : رتبة شخص (بالرد)\n"
    "- <code>مخالفاتي</code> : عدد مخالفاتك\n"
    "- <code>الردود</code> : قائمة الردود\n"
    "- <code>المطور</code> : مطور البوت\n\n"
    f"القوانين: {MAX_WARNS} مخالفات = تقييد يوم، وبعدها اي مخالفة = حظر"
)

HELP_SECTIONS = {
    "prot": (ADMIN,
             "<b>اوامر الحماية</b> (بالرد على رسالة الشخص)\n\n"
             "- <code>حظر</code> / <code>الغاء الحظر</code>\n"
             "- <code>كتم</code> او <code>كتم 30</code> بالدقايق / <code>الغاء الكتم</code>\n"
             "- <code>تقييد</code> : تقييد يوم / <code>الغاء التقييد</code>\n"
             "- <code>انذار</code> : يعطيه مخالفة\n"
             "- <code>المخالفات</code> : عدد مخالفاته\n"
             "- <code>مسح المخالفات</code> : يصفر مخالفاته\n"
             "- <code>كشف</code> : معلومات العضو ومتى غير اسمه\n"
             "  (بالرد، او <code>كشف @يوزر</code>، او <code>كشف ايدي</code>)\n"
             "- <code>فحص</code> : فحص صلاحيات البوت"),
    "ranks": (ADMIN,
              "<b>اوامر الرتب</b> (بالرد)\n\n"
              "- <code>رفع مميز</code> : للادمن وفوق\n"
              "- <code>رفع ادمن</code> : للمدير وفوق\n"
              "- <code>رفع مدير</code> : للمالك\n"
              "- <code>تنزيل</code> : ينزل الشخص من رتبته\n"
              "- <code>الرتب</code> : قائمة اصحاب الرتب\n"
              "- <code>مسح الرتب</code> : للمالك\n\n"
              "المميز: يقدر يرسل روابط وصور حتى لو مقفلة\n"
              "الادمن وفوق: يقدرون يستخدمون اوامر الحماية"),
    "words": (ADMIN,
              "<b>منع الكلمات</b>\n\n"
              "- <code>منع</code> ثم ارسل الكلمة\n"
              "- او <code>منع الكلمة</code> مباشرة\n"
              "- <code>الغاء منع</code> ثم ارسل الكلمة\n"
              "- <code>قائمة المنع</code> : الكلمات الممنوعة\n\n"
              "اي احد يكتب كلمة ممنوعة تنحذف رسالته وياخذ مخالفة\n"
              "اكتب <code>الغاء</code> لالغاء اي عملية"),
    "replies": (ADMIN,
                "<b>الردود</b>\n\n"
                "- <code>اضف رد</code> : البوت يسألك عن الكلمة ثم الرد\n"
                "- <code>مسح رد</code> : يسألك وش الكلمة\n"
                "- <code>الردود</code> : قائمة الردود\n"
                "- <code>مسح الردود</code> : للمدير وفوق\n\n"
                "اكتب <code>الغاء</code> لالغاء اي عملية"),
    "locks": (ADMIN,
              "<b>الاقفال</b>\n\n"
              "- <code>قفل الروابط</code> / <code>فتح الروابط</code>\n"
              "- <code>قفل التوجيه</code> / <code>فتح التوجيه</code>\n"
              "- <code>قفل المعرفات</code> / <code>فتح المعرفات</code>\n"
              "- <code>قفل الصور</code> / <code>فتح الصور</code>\n"
              "- <code>قفل الفيديو</code> / <code>فتح الفيديو</code>\n"
              "- <code>قفل الملصقات</code> / <code>فتح الملصقات</code>\n"
              "- <code>قفل المتحركات</code> / <code>فتح المتحركات</code>\n"
              "- <code>قفل الملفات</code> / <code>فتح الملفات</code>\n"
              "- <code>قفل الصوتيات</code> / <code>فتح الصوتيات</code>\n"
              "- <code>قفل التكرار</code> / <code>فتح التكرار</code>\n"
              "- <code>قفل البوتات</code> / <code>فتح البوتات</code>\n"
              "- <code>قفل الاباحي</code> / <code>فتح الاباحي</code>\n\n"
              "- <code>الاعدادات</code> : حالة كل شي"),
    "features": (ADMIN,
                 "<b>التفعيل والتعطيل</b>\n\n"
                 "- <code>تفعيل</code> : تشغيل البوت بالقروب\n"
                 "- <code>تعطيل</code> : ايقاف البوت (للمدير وفوق)\n\n"
                 "- <code>تفعيل التحقق</code> / <code>تعطيل التحقق</code>\n"
                 "- <code>تفعيل الترحيب</code> / <code>تعطيل الترحيب</code>\n"
                 "- <code>تفعيل الردود</code> / <code>تعطيل الردود</code>\n"
                 "- <code>تفعيل الانذارات</code> / <code>تعطيل الانذارات</code>\n\n"
                 "تقدر تكتب <code>فعل</code> و <code>عطل</code> بدلها"),
}


def help_keyboard(chat_id: int, user_id: int):
    """الازرار مربوطة بالقروب وبالشخص اللي طلب القائمة."""
    def b(label, sec):
        return InlineKeyboardButton(label, callback_data=f"h:{sec}:{chat_id}:{user_id}")
    return InlineKeyboardMarkup([
        [b("الحماية", "prot"), b("الرتب", "ranks")],
        [b("منع الكلمات", "words"), b("الردود", "replies")],
        [b("الاقفال", "locks"), b("التفعيل", "features")],
        [b("اوامر الاعضاء", "member")],
        [InlineKeyboardButton("المطور", url=DEVELOPER_URL)],
    ])


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, section, chat_id, owner = q.data.split(":")
    chat_id, owner = int(chat_id), int(owner)
    if q.from_user.id != owner:
        await q.answer("هذي القائمة مو لك", show_alert=True)
        return
    rank = await get_rank(chat_id, q.from_user.id, context)
    if rank < ADMIN:
        await q.answer("القائمة للمشرفين بس", show_alert=True)
        return
    if section == "member":
        text = MEMBER_HELP
    else:
        min_rank, text = HELP_SECTIONS[section]
        if rank < min_rank:
            await q.answer(f"هذا القسم لـ{RANK_NAMES[min_rank]} وفوق", show_alert=True)
            return
    await q.answer()
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=help_keyboard(chat_id, owner))
    except Exception:
        pass  # نفس النص


# ======================= تنفيذ الاوامر =======================
pending: dict[tuple[int, int], dict] = {}


class Ctx:
    def __init__(self, update, context, rank, arg):
        self.update, self.context, self.rank, self.arg = update, context, rank, arg.strip()
        self.msg = update.effective_message
        self.chat = update.effective_chat
        self.user = update.effective_user
        self.cd = chat_data(self.chat.id)
        self.target = None
        self.target_rank = MEMBER

    async def reply(self, text, **kw):
        return await self.msg.reply_text(text, parse_mode=ParseMode.HTML, **kw)


async def _resolve_target(c: Ctx) -> bool:
    r = c.msg.reply_to_message
    if not r or not r.from_user or r.from_user.is_bot:
        await c.reply("لازم ترد على رسالة الشخص بالامر")
        return False
    c.target = r.from_user
    c.target_rank = await get_rank(c.chat.id, c.target.id, c.context)
    return True


async def _check_above(c: Ctx) -> bool:
    if c.target_rank >= c.rank:
        await c.reply(f"ما تقدر، رتبته {RANK_NAMES[c.target_rank]}")
        return False
    return True


# ---------- اوامر الاعضاء ----------
async def c_help(c: Ctx):
    """قائمة الاوامر: تنرسل للمشرف بالخاص عشان الاعضاء ما يشوفونها."""
    if c.rank < ADMIN:
        return
    title = escape(c.chat.title or "القروب")
    text = f"اوامر قروب <b>{title}</b>\nرتبتك: {RANK_NAMES[c.rank]}\n\nاختار القسم:"
    kb = help_keyboard(c.chat.id, c.user.id)
    try:
        await c.msg.delete()
    except Exception:
        pass
    try:
        await c.context.bot.send_message(c.user.id, text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await send_temp(c.chat, f"{mention(c.user)} ارسلت لك الاوامر بالخاص", seconds=10)
    except Exception:
        # ما بدأ محادثة مع البوت: نرسلها بالقروب وتنحذف بعد دقيقة
        me = await c.context.bot.get_me()
        await send_temp(
            c.chat,
            text + f"\n\n(تنحذف بعد دقيقة، ولو تبيها توصلك بالخاص ابدأ محادثة مع @{me.username})",
            seconds=60,
            reply_markup=kb,
        )


async def c_time(c: Ctx):
    await c.reply(time_text())


async def c_date(c: Ctx):
    await c.reply(date_text())


async def c_id(c: Ctx):
    u = c.target or c.user
    rank = c.target_rank if c.target else c.rank
    w = c.cd["warns"].get(str(u.id), {}).get("count", 0)
    await c.reply(
        f"الاسم: {mention(u)}\n"
        f"الايدي: <code>{u.id}</code>\n"
        f"اليوزر: {'@' + u.username if u.username else 'ما فيه'}\n"
        f"الرتبة: {RANK_NAMES[rank]}\n"
        f"المخالفات: {w} من {MAX_WARNS}"
    )


async def c_myrank(c: Ctx):
    await c.reply(f"رتبتك: {RANK_NAMES[c.rank]}")


async def c_hisrank(c: Ctx):
    await c.reply(f"رتبة {mention(c.target)}: {RANK_NAMES[c.target_rank]}")


async def c_mywarns(c: Ctx):
    w = c.cd["warns"].get(str(c.user.id), {"count": 0, "restricted": False})
    extra = "\nانتبه: تقيدت قبل، اي مخالفة = حظر" if w.get("restricted") else ""
    await c.reply(f"مخالفاتك: {w['count']} من {MAX_WARNS}{extra}")


async def c_dev(c: Ctx):
    await c.reply(f"مطور البوت: {DEVELOPER}", reply_markup=DEV_BUTTON)


async def c_replies(c: Ctx):
    reps = c.cd["replies"]
    if not reps:
        await c.reply("ما فيه ردود مضافة\nالمشرفين يضيفون بامر <code>اضف رد</code>")
        return
    lines = [f"{i}- {escape(v['word'])}" for i, v in enumerate(reps.values(), 1)]
    await c.reply("<b>قائمة الردود:</b>\n\n" + "\n".join(lines))


# ---------- اوامر الحماية ----------
async def c_ban(c: Ctx):
    if not await _check_above(c):
        return
    await c.context.bot.ban_chat_member(c.chat.id, c.target.id)
    await c.reply(f"تم حظر {mention(c.target)}")


async def c_unban(c: Ctx):
    await c.context.bot.unban_chat_member(c.chat.id, c.target.id, only_if_banned=True)
    c.cd["warns"].pop(str(c.target.id), None)
    mark_dirty()
    await c.reply(f"تم الغاء حظر {mention(c.target)}")


async def c_mute(c: Ctx):
    if not await _check_above(c):
        return
    minutes = int(c.arg) if c.arg.isdigit() else None
    await mute(c.context, c.chat.id, c.target.id, minutes)
    await c.reply(f"تم كتم {mention(c.target)} {f'لمدة {minutes} دقيقة' if minutes else 'حتى يتفك'}")


async def c_restrict(c: Ctx):
    if not await _check_above(c):
        return
    await mute(c.context, c.chat.id, c.target.id, RESTRICT_HOURS * 60)
    await c.reply(f"تم تقييد {mention(c.target)} لمدة يوم")


async def c_unmute(c: Ctx):
    await unmute(c.context, c.chat.id, c.target.id)
    await c.reply(f"تم فك القيود عن {mention(c.target)}")


async def c_warn(c: Ctx):
    if not await _check_above(c):
        return
    try:
        await c.msg.reply_to_message.delete()
    except Exception:
        pass
    await violation(c.context, c.chat, c.target, c.arg or "انذار من المشرف")


async def c_warns(c: Ctx):
    w = c.cd["warns"].get(str(c.target.id), {"count": 0, "restricted": False})
    extra = "\nتقيد قبل، المخالفة الجاية حظر" if w.get("restricted") else ""
    await c.reply(f"مخالفات {mention(c.target)}: {w['count']} من {MAX_WARNS}{extra}")


async def c_clear_warns(c: Ctx):
    c.cd["warns"].pop(str(c.target.id), None)
    mark_dirty()
    await c.reply(f"تم مسح مخالفات {mention(c.target)}")


# ---------- اوامر الرتب ----------
def _make_promote(rank_value: int):
    async def fn(c: Ctx):
        if not await _check_above(c):
            return
        c.cd["ranks"][str(c.target.id)] = KEY_OF[rank_value]
        c.cd.setdefault("names", {})[str(c.target.id)] = c.target.full_name
        mark_dirty()
        await c.reply(f"تم رفع {mention(c.target)} الى {RANK_NAMES[rank_value]}")
    return fn


async def c_demote(c: Ctx):
    if not await _check_above(c):
        return
    if c.cd["ranks"].pop(str(c.target.id), None) is None:
        await c.reply(f"{mention(c.target)} ما عنده رتبة بالبوت")
        return
    mark_dirty()
    await c.reply(f"تم تنزيل {mention(c.target)} وصار عضو")


async def c_ranks_list(c: Ctx):
    groups = defaultdict(list)
    for uid, key in c.cd["ranks"].items():
        groups[RANK_KEYS[key]].append(uid)
    if not groups:
        await c.reply("ما فيه رتب مضافة")
        return
    text = "<b>الرتب:</b>\n"
    for r in (MANAGER, ADMIN, VIP):
        if groups[r]:
            text += f"\n{RANK_NAMES[r]}:\n" + "\n".join(
                f'- <a href="tg://user?id={u}">{escape(c.cd.get("names", {}).get(u, u))}</a>'
                for u in groups[r]
            ) + "\n"
    await c.reply(text)


async def c_clear_ranks(c: Ctx):
    c.cd["ranks"].clear()
    mark_dirty()
    await c.reply("تم مسح كل الرتب")


# ---------- الردود ----------
async def c_add_reply(c: Ctx):
    pending[(c.chat.id, c.user.id)] = {"step": "reply_word", "t": time.time()}
    await c.reply("ارسل <b>الكلمة</b> اللي تبي البوت يرد عليها\nاو اكتب <code>الغاء</code>")


async def c_del_reply(c: Ctx):
    if c.arg:
        await _do_del_reply(c, c.arg)
        return
    pending[(c.chat.id, c.user.id)] = {"step": "del_reply", "t": time.time()}
    await c.reply("ارسل الكلمة اللي تبي تمسح ردها")


async def _do_del_reply(c: Ctx, word: str):
    if c.cd["replies"].pop(simple_norm(word), None):
        mark_dirty()
        await c.reply(f"تم مسح الرد: {escape(word)}")
    else:
        await c.reply("ما لقيت رد بهالكلمة")


async def c_clear_replies(c: Ctx):
    c.cd["replies"].clear()
    mark_dirty()
    await c.reply("تم مسح كل الردود")


# ---------- منع الكلمات ----------
async def c_add_word(c: Ctx):
    if c.arg:
        await _do_add_word(c, c.arg)
        return
    pending[(c.chat.id, c.user.id)] = {"step": "add_word", "t": time.time()}
    await c.reply("ارسل الكلمة اللي تبي تمنعها\nاو اكتب <code>الغاء</code>")


async def _do_add_word(c: Ctx, word: str):
    try:
        await c.msg.delete()
    except Exception:
        pass
    norm = normalize(word)
    c.cd["unwords"] = [w for w in c.cd["unwords"] if normalize(w) != norm]
    if norm not in {normalize(w) for w in c.cd["words"]}:
        c.cd["words"].append(word)
    mark_dirty()
    await c.chat.send_message(
        "تم منع الكلمة\nاي احد يكتبها تنحذف رسالته وياخذ مخالفة",
        parse_mode=ParseMode.HTML,
    )


async def c_del_word(c: Ctx):
    if c.arg:
        await _do_del_word(c, c.arg)
        return
    pending[(c.chat.id, c.user.id)] = {"step": "del_word", "t": time.time()}
    await c.reply("ارسل الكلمة اللي تبي تلغي منعها")


async def _do_del_word(c: Ctx, word: str):
    norm = normalize(word)
    before = len(c.cd["words"])
    c.cd["words"] = [w for w in c.cd["words"] if normalize(w) != norm]
    if len(c.cd["words"]) == before:
        if norm in GLOBAL_WORDS:
            c.cd["unwords"].append(word)
        else:
            await c.reply("هالكلمة مو ممنوعة")
            return
    mark_dirty()
    await c.reply("تم الغاء منع الكلمة")


async def c_list_words(c: Ctx):
    added = c.cd["words"]
    text = f"<b>قائمة المنع</b>\n\nالكلمات الاساسية: {len(GLOBAL_WORDS)} كلمة"
    if added:
        text += (f"\nالكلمات المضافة ({len(added)}):\n<tg-spoiler>"
                 + "، ".join(escape(w) for w in added) + "</tg-spoiler>")
    else:
        text += "\nما فيه كلمات مضافة\nتقدر تضيف بامر <code>منع</code>"
    await c.reply(text)


# ---------- الاقفال والتفعيل ----------
async def c_lock(c: Ctx, value: bool):
    key = LOCK_ALIASES.get(simple_norm(c.arg))
    if not key:
        if FEATURE_ALIASES.get(simple_norm(c.arg)):
            await c.reply(f"استخدم <code>{'تعطيل' if value else 'تفعيل'} {escape(c.arg)}</code>")
        else:
            await c.reply("اكتب وش تبي تقفل، مثلا: <code>قفل الروابط</code>\nاكتب <code>الاعدادات</code> تشوف الكل")
        return
    c.cd["locks"][key] = value
    mark_dirty()
    await c.reply(f"{'تم قفل' if value else 'تم فتح'} {LOCKS[key][0]}")


async def c_enable(c: Ctx, value: bool):
    if not c.arg:
        if not value and c.rank < MANAGER:
            await c.reply("تعطيل البوت للمدير وفوق")
            return
        if c.cd["active"] == value:
            await c.reply("البوت مفعل من قبل" if value else "البوت معطل من قبل")
            return
        c.cd["active"] = value
        mark_dirty()
        if value:
            await c.reply(f"تم تفعيل البوت في {escape(c.chat.title or 'القروب')}\n"
                          "اكتب <code>الاوامر</code> او / تشوف كل شي",
                          reply_markup=DEV_BUTTON)
        else:
            await c.reply("تم تعطيل البوت، اكتب <code>تفعيل</code> عشان ترجعه")
        return
    key = FEATURE_ALIASES.get(simple_norm(c.arg))
    if not key:
        lock = LOCK_ALIASES.get(simple_norm(c.arg))
        if lock:
            c.cd["locks"][lock] = not value
            mark_dirty()
            await c.reply(f"{'تم فتح' if value else 'تم قفل'} {LOCKS[lock][0]}")
        else:
            await c.reply("ما عرفت وش تبي تفعل\nالمتاح: التحقق، الترحيب، الردود، الانذارات")
        return
    c.cd["features"][key] = value
    mark_dirty()
    await c.reply(f"{'تم تفعيل' if value else 'تم تعطيل'} {FEATURES[key][0]}")


async def c_settings(c: Ctx):
    on, off = "مقفل", "مفتوح"
    lines = [f"- {name}: {on if c.cd['locks'][k] else off}" for k, (name, _) in LOCKS.items()]
    feats = [f"- {name}: {'مفعل' if c.cd['features'][k] else 'معطل'}" for k, (name, _) in FEATURES.items()]
    await c.reply(
        "<b>الاعدادات</b>\n\n<b>الاقفال:</b>\n" + "\n".join(lines)
        + "\n\n<b>المميزات:</b>\n" + "\n".join(feats)
        + f"\n\nالمخالفات: {MAX_WARNS} ثم تقييد {RESTRICT_HOURS} ساعة ثم حظر"
    )


STATUS_NAMES = {
    ChatMemberStatus.OWNER: "مالك القروب",
    ChatMemberStatus.ADMINISTRATOR: "مشرف",
    ChatMemberStatus.MEMBER: "عضو",
    ChatMemberStatus.RESTRICTED: "مقيد",
    ChatMemberStatus.LEFT: "طالع من القروب",
    ChatMemberStatus.BANNED: "محظور",
}


async def c_kashf(c: Ctx):
    # الهدف: بالرد، او كشف ايدي، او كشف @يوزر، او نفسه
    uid = None
    r = c.msg.reply_to_message
    if r and r.from_user and not r.from_user.is_bot:
        uid = r.from_user.id
        track_user(r.from_user)
    elif c.arg:
        uid = find_user_id(c.arg)
        if uid is None:
            await c.reply("ما لقيت هالشخص\nرد على رسالته بكلمة <code>كشف</code> او اكتب ايديه")
            return
    else:
        uid = c.user.id

    status_txt, until_txt, tg_user = "غير معروف", "", None
    try:
        cm = await c.context.bot.get_chat_member(c.chat.id, uid)
        tg_user = cm.user
        track_user(tg_user)
        status_txt = STATUS_NAMES.get(cm.status, cm.status)
        until = getattr(cm, "until_date", None)
        if cm.status in (ChatMemberStatus.RESTRICTED, ChatMemberStatus.BANNED) and until:
            until_txt = f"\n- القيد ينتهي: {fmt_time(until.timestamp())}"
    except Exception as e:
        log.warning("kashf get member failed: %s", e)

    u = DATA.get("users", {}).get(str(uid), {})
    st = c.cd.get("stats", {}).get(str(uid), {})
    w = c.cd["warns"].get(str(uid), {"count": 0, "restricted": False})
    rank = await get_rank(c.chat.id, uid, c.context)
    name = (tg_user.full_name if tg_user else u.get("name")) or str(uid)
    username = (tg_user.username if tg_user else u.get("username")) or ""

    bio = ""
    try:
        info = await c.context.bot.get_chat(uid)
        if getattr(info, "bio", None):
            bio = f"\n- النبذة: {escape(info.bio)}"
    except Exception:
        pass

    if u.get("name_changed"):
        old = u.get("old_names", [{}])[0].get("name", "")
        name_change = f"{fmt_time(u['name_changed'])}\n   كان اسمه: {escape(old) or 'بدون'}"
    elif u:
        name_change = f"ما غيره من يوم ما شافه البوت ({fmt_time(u.get('first_seen'))})"
    else:
        name_change = "ما فيه بيانات، البوت ما شافه قبل"

    if u.get("user_changed"):
        old_u = u.get("old_usernames", [{}])[0].get("username", "")
        user_change = f"{fmt_time(u['user_changed'])}\n   كان: {'@' + old_u if old_u else 'بدون يوزر'}"
    else:
        user_change = "ما غيره"

    olds = u.get("old_names", [])
    old_list = ""
    if len(olds) > 1:
        old_list = "\n- اسماؤه السابقة: " + "، ".join(escape(o["name"]) for o in olds if o.get("name"))

    text = (
        "<b>كشف العضو</b>\n\n"
        f'- الاسم: <a href="tg://user?id={uid}">{escape(name)}</a>\n'
        f"- الايدي: <code>{uid}</code>\n"
        f"- اليوزر: {'@' + username if username else 'ما فيه'}\n"
        f"- الرتبة: {RANK_NAMES[rank]}\n"
        f"- حالته بالقروب: {status_txt}{until_txt}\n"
        f"- المخالفات: {w['count']} من {MAX_WARNS}{' (تقيد قبل)' if w.get('restricted') else ''}\n"
        f"- عدد رسائله: {st.get('msgs', 0)}\n"
        f"- آخر رسالة: {fmt_time(st.get('last_msg'))}\n"
        f"- دخل القروب: {fmt_time(st.get('joined')) if st.get('joined') else 'قبل تشغيل البوت'}"
        f"{bio}\n\n"
        f"<b>آخر تغيير للاسم:</b>\n   {name_change}{old_list}\n\n"
        f"<b>آخر تغيير لليوزر:</b>\n   {user_change}"
    )
    await c.reply(text)


async def c_check(c: Ctx):
    me = await c.context.bot.get_chat_member(c.chat.id, c.context.bot.id)
    ok, bad = "تمام", "ناقص"
    is_admin = me.status == ChatMemberStatus.ADMINISTRATOR
    can_del = is_admin and bool(getattr(me, "can_delete_messages", False))
    can_ban = is_admin and bool(getattr(me, "can_restrict_members", False))
    is_super = c.chat.type == ChatType.SUPERGROUP
    lines = [
        f"- نوع القروب: {'سوبر قروب ' + ok if is_super else 'قروب عادي ' + bad}",
        f"- البوت مشرف: {ok if is_admin else bad}",
        f"- صلاحية حذف الرسائل: {ok if can_del else bad}",
        f"- صلاحية حظر المستخدمين: {ok if can_ban else bad}",
        f"- البوت مفعل: {ok if c.cd['active'] else bad}",
    ]
    text = "<b>فحص البوت</b>\n\n" + "\n".join(lines)
    if not is_super:
        text += "\n\n" + SUPERGROUP_HELP
    elif not (is_admin and can_del and can_ban):
        text += ("\n\nادخل اعدادات القروب ثم المشرفين ثم البوت، وفعل:\n"
                 "- حذف الرسائل\n- حظر المستخدمين")
    else:
        text += "\n\nكل شي تمام، البوت يقدر يكتم ويقيد ويتحقق"
    await c.reply(text)


# ---------- جدول الاوامر ----------
# الامر: (اقل رتبة، الدالة، يحتاج رد؟)
COMMANDS = {
    "الاوامر": (ADMIN, c_help, False),
    "اوامر": (ADMIN, c_help, False),
    "الوقت": (MEMBER, c_time, False),
    "الساعه": (MEMBER, c_time, False),
    "التاريخ": (MEMBER, c_date, False),
    "ايدي": (MEMBER, c_id, False),
    "رتبتي": (MEMBER, c_myrank, False),
    "رتبته": (MEMBER, c_hisrank, True),
    "مخالفاتي": (MEMBER, c_mywarns, False),
    "المطور": (MEMBER, c_dev, False),
    "الردود": (MEMBER, c_replies, False),

    "حظر": (ADMIN, c_ban, True),
    "الغاء الحظر": (ADMIN, c_unban, True),
    "الغاء حظر": (ADMIN, c_unban, True),
    "كتم": (ADMIN, c_mute, True),
    "الغاء الكتم": (ADMIN, c_unmute, True),
    "الغاء كتم": (ADMIN, c_unmute, True),
    "تقييد": (ADMIN, c_restrict, True),
    "الغاء التقييد": (ADMIN, c_unmute, True),
    "الغاء تقييد": (ADMIN, c_unmute, True),
    "انذار": (ADMIN, c_warn, True),
    "تحذير": (ADMIN, c_warn, True),
    "المخالفات": (ADMIN, c_warns, True),
    "مسح المخالفات": (ADMIN, c_clear_warns, True),

    "رفع مميز": (ADMIN, _make_promote(VIP), True),
    "رفع ادمن": (MANAGER, _make_promote(ADMIN), True),
    "رفع مدير": (OWNER, _make_promote(MANAGER), True),
    "تنزيل": (ADMIN, c_demote, True),
    "تنزيل مميز": (ADMIN, c_demote, True),
    "تنزيل ادمن": (MANAGER, c_demote, True),
    "تنزيل مدير": (OWNER, c_demote, True),
    "الرتب": (ADMIN, c_ranks_list, False),
    "مسح الرتب": (OWNER, c_clear_ranks, False),

    "اضف رد": (ADMIN, c_add_reply, False),
    "مسح رد": (ADMIN, c_del_reply, False),
    "حذف رد": (ADMIN, c_del_reply, False),
    "مسح الردود": (MANAGER, c_clear_replies, False),

    "منع": (ADMIN, c_add_word, False),
    "اضف كلمه": (ADMIN, c_add_word, False),
    "الغاء منع": (ADMIN, c_del_word, False),
    "مسح كلمه": (ADMIN, c_del_word, False),
    "قائمه المنع": (ADMIN, c_list_words, False),
    "الكلمات الممنوعه": (ADMIN, c_list_words, False),

    "قفل": (ADMIN, lambda c: c_lock(c, True), False),
    "فتح": (ADMIN, lambda c: c_lock(c, False), False),
    "تفعيل": (ADMIN, lambda c: c_enable(c, True), False),
    "تعطيل": (ADMIN, lambda c: c_enable(c, False), False),
    "الاعدادات": (ADMIN, c_settings, False),
    "فحص": (ADMIN, c_check, False),
    "كشف": (ADMIN, c_kashf, False),
}
_CMD_ORDER = sorted(COMMANDS, key=len, reverse=True)
SILENT_FOR_MEMBERS = {"الاوامر", "اوامر"}


def _canonical(t: str) -> str:
    """فعل الترحيب -> تفعيل الترحيب"""
    for short, full in (("فعل", "تفعيل"), ("عطل", "تعطيل")):
        if t == short or t.startswith(short + " "):
            return full + t[len(short):]
    return t


def match_command(raw: str):
    """يرجع (الامر، النص بعده) او None."""
    t = _canonical(simple_norm(raw))
    if not t:
        return None
    for key in _CMD_ORDER:
        if t == key or t.startswith(key + " "):
            arg = " ".join(raw.split()[len(key.split()):])
            return key, arg
    return None


async def run_command(update: Update, context, rank: int, raw: str) -> bool:
    found = match_command(raw)
    if not found:
        return False
    key, arg = found
    msg = update.effective_message
    min_rank, fn, needs_target = COMMANDS[key]
    if rank < min_rank:
        if key in SILENT_FOR_MEMBERS:
            return True  # قائمة الاوامر ما تطلع للاعضاء ابدا
        # نرد بس لو الرسالة هي الامر نفسه بدون كلام زايد
        if not arg:
            await msg.reply_text(f"هذا الامر لـ{RANK_NAMES[min_rank]} وفوق")
            return True
        return False
    c = Ctx(update, context, rank, arg)
    if needs_target and not await _resolve_target(c):
        return True
    try:
        await fn(c)
    except Exception as e:
        log.warning("command %s failed: %s", key, e)
        await msg.reply_text(explain_error(e), parse_mode=ParseMode.HTML)
    return True


async def try_pending(update: Update, context, rank: int) -> bool:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    key = (chat.id, user.id)
    p = pending.get(key)
    if not p or not msg.text:
        return False
    if time.time() - p["t"] > 300:
        pending.pop(key, None)
        return False
    text = msg.text.strip()
    c = Ctx(update, context, rank, "")
    if simple_norm(text) == "الغاء":
        pending.pop(key, None)
        await c.reply("تم الالغاء")
        return True

    step = p["step"]
    if step == "reply_word":
        p.update(step="reply_text", word=text, t=time.time())
        await c.reply(f"الكلمة: <b>{escape(text)}</b>\nالحين ارسل <b>الرد</b>")
    elif step == "reply_text":
        pending.pop(key, None)
        c.cd["replies"][simple_norm(p["word"])] = {"word": p["word"], "reply": msg.text_html}
        mark_dirty()
        await c.reply(f"تم اضافة الرد\n\nالكلمة: {escape(p['word'])}\nالرد: {msg.text_html}")
    elif step == "del_reply":
        pending.pop(key, None)
        await _do_del_reply(c, text)
    elif step == "add_word":
        pending.pop(key, None)
        await _do_add_word(c, text)
    elif step == "del_word":
        pending.pop(key, None)
        await _do_del_word(c, text)
    return True


def greeting_for(text: str) -> str | None:
    t = simple_norm(text)
    for trigger, answer in GREETINGS:
        if t == trigger or (len(trigger) > 4 and t.startswith(trigger)):
            return answer
    return None


async def try_auto_reply(update: Update, context) -> bool:
    msg, chat = update.effective_message, update.effective_chat
    cd = chat_data(chat.id)
    if not msg.text or not cd["features"]["replies"]:
        return False
    custom = cd["replies"].get(simple_norm(msg.text))
    if custom:
        await msg.reply_text(custom["reply"], parse_mode=ParseMode.HTML)
        return True
    answer = greeting_for(msg.text)
    if answer:
        await msg.reply_text(answer)
        return True
    return False


# ======================= معالج رسائل القروب =======================
async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not msg or not user:
        return
    cd = chat_data(chat.id)
    rank = await sender_rank(update, context)
    edited = update.edited_message is not None
    track_user(user, chat.id, count_msg=not edited)
    if msg.reply_to_message and msg.reply_to_message.from_user:
        track_user(msg.reply_to_message.from_user)

    # البوت غير مفعل: نسمع بس لامر التفعيل
    if not cd["active"]:
        if not edited and msg.text and simple_norm(msg.text) in ("تفعيل", "فعل"):
            if rank >= ADMIN:
                await run_command(update, context, rank, "تفعيل")
            else:
                await msg.reply_text("تفعيل البوت للمشرفين بس")
        return

    # المشرفين: اوامرهم اول، بعدين فحص الكلمات الممنوعة (بدون عقوبة)
    if rank >= ADMIN:
        if not edited:
            if await try_pending(update, context, rank):
                return
            if msg.text and await run_command(update, context, rank, msg.text):
                return
        if await check_violation(update, context, rank):
            try:
                await msg.delete()
                await send_temp(chat, f"تم حذف رسالة {mention(user)} لانها فيها كلمة ممنوعة", seconds=20)
            except Exception:
                pass
            return
        if not edited:
            await try_auto_reply(update, context)
        return

    # الاعضاء: الفحص اول
    reason = await check_violation(update, context, rank)
    if reason:
        try:
            await msg.delete()
        except Exception as e:
            log.warning("delete failed: %s", e)
        await violation(context, chat, user, reason)
        return
    if edited:
        return
    if msg.text and await run_command(update, context, rank, msg.text):
        return
    await try_auto_reply(update, context)


# ======================= الاوامر الجاهزة / =======================
SLASH = {
    "help": ("الاوامر", "قائمة الاوامر"),
    "time": ("الوقت", "الساعة الحين"),
    "date": ("التاريخ", "التاريخ هجري وميلادي"),
    "id": ("ايدي", "معلوماتك او معلومات شخص بالرد"),
    "rank": ("رتبتي", "رتبتك بالقروب"),
    "mywarns": ("مخالفاتي", "عدد مخالفاتك"),
    "replies": ("الردود", "قائمة الردود"),
    "dev": ("المطور", "مطور البوت"),
    "enable": ("تفعيل", "تفعيل البوت بالقروب"),
    "disable": ("تعطيل", "تعطيل البوت بالقروب"),
    "ban": ("حظر", "حظر بالرد"),
    "unban": ("الغاء الحظر", "الغاء حظر بالرد"),
    "mute": ("كتم", "كتم بالرد"),
    "unmute": ("الغاء الكتم", "الغاء كتم بالرد"),
    "restrict": ("تقييد", "تقييد يوم بالرد"),
    "warn": ("انذار", "اعطاء مخالفة بالرد"),
    "warns": ("المخالفات", "مخالفات شخص بالرد"),
    "clearwarns": ("مسح المخالفات", "تصفير مخالفات شخص بالرد"),
    "filter": ("منع", "منع كلمة"),
    "unfilter": ("الغاء منع", "الغاء منع كلمة"),
    "filters": ("قائمه المنع", "الكلمات الممنوعة"),
    "addreply": ("اضف رد", "اضافة رد"),
    "delreply": ("مسح رد", "مسح رد"),
    "lock": ("قفل", "قفل شي مثل: /lock الروابط"),
    "unlock": ("فتح", "فتح شي مثل: /unlock الصور"),
    "settings": ("الاعدادات", "حالة الاقفال والمميزات"),
    "check": ("فحص", "فحص صلاحيات البوت"),
    "info": ("كشف", "كشف معلومات عضو بالرد"),
    "vip": ("رفع مميز", "رفع مميز بالرد"),
    "admin": ("رفع ادمن", "رفع ادمن بالرد"),
    "manager": ("رفع مدير", "رفع مدير بالرد"),
    "demote": ("تنزيل", "تنزيل رتبة بالرد"),
    "ranks": ("الرتب", "قائمة الرتب"),
}
MEMBER_SLASH = ["help", "time", "date", "id", "rank", "mywarns", "replies", "dev"]


async def on_slash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    if not msg or not msg.text:
        return
    name = msg.text.split()[0][1:].split("@")[0].lower()
    if name not in SLASH:
        return
    if chat.type == ChatType.PRIVATE:
        await on_private(update, context, forced=SLASH[name][0])
        return
    cd = chat_data(chat.id)
    rank = await sender_rank(update, context)
    if not cd["active"] and name != "enable":
        if rank >= ADMIN:
            await msg.reply_text("البوت مو مفعل، اكتب <code>تفعيل</code>", parse_mode=ParseMode.HTML)
        return
    raw = SLASH[name][0] + (" " + " ".join(context.args) if context.args else "")
    await run_command(update, context, rank, raw)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != ChatType.PRIVATE:
        if not chat_data(chat.id)["active"]:
            return
        rank = await sender_rank(update, context)
        if rank >= ADMIN:
            await c_help(Ctx(update, context, rank, ""))
        return
    me = await context.bot.get_me()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("اضفني لقروبك", url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("المطور", url=DEVELOPER_URL)],
    ])
    await update.effective_message.reply_text(
        "<b>بوت حماية القروبات</b>\n\n"
        "- يحذف الكلام البذيء والصور الاباحية\n"
        "- يمنع الروابط والتوجيه والسبام\n"
        f"- مخالفات: {MAX_WARNS} تحذيرات ثم تقييد يوم ثم حظر\n"
        "- رتب: مالك، مدير، ادمن، مميز\n"
        "- ردود تلقائية وردود تضيفها بنفسك\n"
        "- الوقت والتاريخ هجري وميلادي\n\n"
        "<b>طريقة التشغيل:</b>\n"
        "1- اضفني لقروبك\n"
        "2- ارفعني مشرف بصلاحية حذف الرسائل وحظر الاعضاء\n"
        "3- اكتب في القروب: <code>تفعيل</code>\n\n"
        f"المطور: {DEVELOPER}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def on_private(update: Update, context: ContextTypes.DEFAULT_TYPE, forced: str | None = None):
    msg = update.effective_message
    t = simple_norm(forced or msg.text or "")
    if t in ("الوقت", "الساعه"):
        await msg.reply_text(time_text(), parse_mode=ParseMode.HTML)
    elif t == "التاريخ":
        await msg.reply_text(date_text(), parse_mode=ParseMode.HTML)
    elif t == "المطور":
        await msg.reply_text(f"المطور: {DEVELOPER}", reply_markup=DEV_BUTTON)
    elif t in ("الاوامر", "اوامر"):
        await msg.reply_text(MEMBER_HELP, parse_mode=ParseMode.HTML, reply_markup=DEV_BUTTON)
    elif t == "ايدي":
        u = update.effective_user
        await msg.reply_text(f"الاسم: {mention(u)}\nالايدي: <code>{u.id}</code>", parse_mode=ParseMode.HTML)
    elif forced:
        await msg.reply_text("هذا الامر يشتغل داخل القروب بس")
    else:
        answer = greeting_for(msg.text or "")
        if answer:
            await msg.reply_text(answer)


# ======================= دخول الاعضاء والتحقق =======================
pending_captcha: dict[tuple[int, int], int] = {}

JOIN_FROM = {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}
JOIN_TO = {ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED}


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يشتغل لما احد يدخل القروب (رابط او اضافة)."""
    cmu = update.chat_member
    old, new = cmu.old_chat_member, cmu.new_chat_member
    if old.status not in JOIN_FROM or new.status not in JOIN_TO:
        return
    if new.status == ChatMemberStatus.RESTRICTED and not getattr(new, "is_member", True):
        return
    chat, member = update.effective_chat, new.user
    track_user(member, chat.id, joined=True)
    cd = chat_data(chat.id)
    if not cd["active"]:
        return
    adder = cmu.from_user
    adder_rank = await get_rank(chat.id, adder.id, context) if adder else MEMBER

    if member.is_bot:
        if cd["locks"]["bots"] and adder_rank < ADMIN:
            try:
                await context.bot.ban_chat_member(chat.id, member.id)
                await send_temp(chat, "تم طرد بوت لان البوتات مقفلة\nبس المشرفين يقدرون يضيفون بوتات")
            except Exception as e:
                log.warning("ban bot failed: %s", e)
        return

    if find_bad_word(member.full_name, words_for(chat.id)) or LINK_RE.search(member.full_name or ""):
        try:
            await context.bot.ban_chat_member(chat.id, member.id)
            await send_temp(chat, "تم طرد عضو جديد لان اسمه فيه كلام ممنوع او رابط")
        except Exception as e:
            log.warning("ban name failed: %s", e)
        return

    if cd["features"]["captcha"]:
        try:
            await mute(context, chat.id, member.id)
        except Exception as e:
            log.warning("captcha restrict failed: %s", e)
            await send_temp(chat, "ما قدرت اقيد العضو الجديد للتحقق\n\n" + explain_error(e), seconds=300)
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("انا مو بوت", callback_data=f"v:{member.id}")]])
        cap = await chat.send_message(
            f"اهلا {mention(member)}\n"
            f"اضغط الزر خلال {CAPTCHA_SECONDS // 60} دقايق عشان تقدر تكتب\n"
            f"اذا ما ضغطت بتنطرد",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        pending_captcha[(chat.id, member.id)] = cap.message_id
        asyncio.get_running_loop().create_task(captcha_timeout(context, chat.id, member.id, cap))
    elif cd["features"]["welcome"]:
        await send_welcome(chat, member)


async def send_welcome(chat, member):
    await send_temp(
        chat,
        f"اهلا وسهلا {mention(member)} في {escape(chat.title or 'القروب')}\n\n"
        f"القوانين:\n"
        f"- ممنوع الكلام البذيء والروابط\n"
        f"- {MAX_WARNS} مخالفات = تقييد يوم\n"
        f"- اي مخالفة بعد التقييد = حظر",
        seconds=300,
    )


async def captcha_timeout(context, chat_id, user_id, cap_msg):
    await asyncio.sleep(CAPTCHA_SECONDS)
    if pending_captcha.pop((chat_id, user_id), None) is None:
        return
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.unban_chat_member(chat_id, user_id)
    except Exception as e:
        log.warning("captcha kick failed: %s", e)
    try:
        await cap_msg.delete()
    except Exception:
        pass


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    target = int(q.data.split(":")[1])
    if q.from_user.id != target:
        await q.answer("هذا الزر مو لك", show_alert=True)
        return
    chat = q.message.chat
    pending_captcha.pop((chat.id, target), None)
    try:
        await unmute(context, chat.id, target)
    except Exception as e:
        log.warning("verify unmute failed: %s", e)
    await q.answer("تم التحقق، حياك")
    try:
        await q.message.delete()
    except Exception:
        pass
    if chat_data(chat.id)["features"]["welcome"]:
        await send_welcome(chat, q.from_user)


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لما البوت ينضاف للقروب او يترفع مشرف."""
    cmu = update.my_chat_member
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return
    old, new = cmu.old_chat_member.status, cmu.new_chat_member.status
    cd = chat_data(chat.id)
    _tg_cache.pop(chat.id, None)
    try:
        if old in JOIN_FROM and new in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
            text = "شكرا لاضافتي\n\n"
            if new != ChatMemberStatus.ADMINISTRATOR:
                text += "1- ارفعني مشرف بصلاحية حذف الرسائل وحظر الاعضاء\n2- "
            text += "اكتب <code>تفعيل</code> عشان ابدأ احمي القروب"
            if chat.type == ChatType.GROUP:
                text += "\n\nتنبيه: " + SUPERGROUP_HELP
            await chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=DEV_BUTTON)
        elif old == ChatMemberStatus.MEMBER and new == ChatMemberStatus.ADMINISTRATOR:
            if cd["active"]:
                await chat.send_message("تم رفعي مشرف، الحماية شغالة")
            else:
                await chat.send_message("تم رفعي مشرف\nاكتب <code>تفعيل</code> عشان ابدأ",
                                        parse_mode=ParseMode.HTML)
    except Exception as e:
        log.warning("my_chat_member failed: %s", e)


async def on_migrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لما القروب يتحول سوبر قروب، ننقل كل البيانات للايدي الجديد."""
    msg = update.effective_message
    old_id, new_id = msg.chat.id, msg.migrate_to_chat_id
    if not new_id:
        return
    chats = DATA.setdefault("chats", {})
    if str(old_id) in chats:
        chats[str(new_id)] = chats.pop(str(old_id))
        mark_dirty()
    _tg_cache.pop(old_id, None)
    try:
        await context.bot.send_message(
            new_id, "تم تحويل القروب لسوبر قروب، الحين الكتم والتقييد والتحقق كلها تشتغل",
        )
    except Exception as e:
        log.warning("migrate msg failed: %s", e)


async def on_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف رسائل دخل فلان وطلع فلان."""
    if chat_data(update.effective_chat.id)["active"]:
        try:
            await update.effective_message.delete()
        except Exception:
            pass


# ======================= التشغيل =======================
async def post_init(app: Application):
    asyncio.get_running_loop().create_task(saver_loop())
    try:
        admin_cmds = [BotCommand(n, d) for n, (_, d) in SLASH.items()]
        private_cmds = [BotCommand("start", "البداية"), BotCommand("time", "الساعة الحين"),
                        BotCommand("date", "التاريخ"), BotCommand("id", "الايدي"),
                        BotCommand("dev", "المطور")]
        await app.bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
        await app.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
        await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeAllChatAdministrators())
        log.info("commands menu set")
    except Exception as e:
        log.warning("set commands failed: %s", e)


async def post_shutdown(app: Application):
    write_data()
    if GIT_SAVE:
        git_push()
    log.info("data saved")


def build_app() -> Application:
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    groups = filters.ChatType.GROUPS
    private = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.COMMAND, on_slash))
    app.add_handler(CallbackQueryHandler(verify, pattern=r"^v:\d+$"))
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^h:\w+:-?\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: u.callback_query.answer("هذي قائمة قديمة، اكتب الاوامر من جديد", show_alert=True),
        pattern=r"^h:\w+$",
    ))
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE, on_migrate))
    app.add_handler(MessageHandler(
        groups & (filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER),
        on_service_message,
    ))
    app.add_handler(MessageHandler(groups & ~filters.StatusUpdate.ALL, on_group_message))
    app.add_handler(MessageHandler(private & filters.TEXT, on_private))
    return app


def main():
    app = build_app()
    log.info("bot running - %d words, %d chats", len(GLOBAL_WORDS), len(DATA.get("chats", {})))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
