"""
بوت حماية قروبات تلقرام — النسخة الكاملة
==========================================
المطور: @amjd_134

- رتب: المطور / المالك / المدير / الأدمن / المميز / العضو
- أوامر عربية بدون شرطة (حظر، كتم، رفع ادمن...) وكل رتبة تشوف أوامرها
- نظام مخالفات: 3 تحذيرات ← تقييد يوم ← تكرار بعدها = حظر نهائي
- فلتر كلمات بذيئة ذكي + فحص الصور الإباحية + الروابط + التوجيه + التكرار
- ردود تلقائية (السلام، صباح الخير...) + ردود مخصصة تضيفها بنفسك
- الوقت والتاريخ (هجري وميلادي) بتوقيت مكة
- تحقق للأعضاء الجدد
- كل البيانات تنحفظ في data.json وترتفع لـ GitHub تلقائياً
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
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, MessageEntityType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("guard")

TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID") or 0)   # اختياري: ايديك عشان تصير "المطور" بكل القروبات
GIT_SAVE = os.environ.get("GIT_SAVE") == "1"
BASE = Path(__file__).parent
DATA_FILE = BASE / "data.json"
WORDS_FILE = BASE / "words.txt"
TZ = ZoneInfo("Asia/Riyadh")

# ======================= الإعدادات =======================
MAX_WARNS = 3              # عدد التحذيرات قبل التقييد
RESTRICT_HOURS = 24        # مدة التقييد بعد التحذيرات
FLOOD_LIMIT = 5            # رسائل
FLOOD_WINDOW = 10          # خلال كم ثانية
CAPTCHA_SECONDS = 120
NSFW_THRESHOLD = 0.45
NOTICE_DELETE_AFTER = 120  # تنحذف رسائل التحذير بعد دقيقتين عشان القروب يبقى نظيف

DEVELOPER = "@amjd_134"
DEVELOPER_URL = "https://t.me/amjd_134"
DEV_BUTTON = InlineKeyboardMarkup([[InlineKeyboardButton("👨‍💻 المطور", url=DEVELOPER_URL)]])

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.(me|dog)/|\b[\w-]+\.(com|net|org|me|io|xyz|link|ly|site|online|top)\b)",
    re.IGNORECASE,
)

# ======================= الرتب =======================
DEV, OWNER, MANAGER, ADMIN, VIP, MEMBER = 5, 4, 3, 2, 1, 0
RANK_NAMES = {
    DEV: "المطور 👨‍💻",
    OWNER: "المالك 👑",
    MANAGER: "المدير 🎖",
    ADMIN: "الأدمن ⭐",
    VIP: "المميز 💎",
    MEMBER: "عضو 👤",
}
RANK_KEYS = {"manager": MANAGER, "admin": ADMIN, "vip": VIP}
KEY_OF = {v: k for k, v in RANK_KEYS.items()}

# ======================= الردود التلقائية =======================
GREETINGS = [
    ("السلام عليكم", "وعليكم السلام ورحمة الله وبركاته 🌹"),
    ("سلام عليكم", "وعليكم السلام ورحمة الله وبركاته 🌹"),
    ("صباح الخير", "صباح النور والسرور ☀️"),
    ("مساء الخير", "مساء النور والورد 🌙"),
    ("هلا", "هلا والله 👋"),
    ("مرحبا", "مراحب 🌷"),
    ("شكرا", "العفو 🤍"),
    ("باي", "في أمان الله 👋"),
    ("تصبحون على خير", "وأنت من أهل الخير 🌙"),
]

DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
G_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
            "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
H_MONTHS = ["محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة",
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
    c.setdefault("ranks", {})
    c.setdefault("replies", {})
    c.setdefault("words", [])
    c.setdefault("unwords", [])
    c.setdefault("warns", {})
    c.setdefault("locks", {"links": True, "forward": True, "captcha": True})
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
        await asyncio.sleep(30)
        if _dirty:
            _dirty = False
            write_data()
            need_push = True
        if GIT_SAVE and need_push and time.time() - last_push > 120:
            await asyncio.to_thread(git_push)
            need_push, last_push = False, time.time()


# ======================= تطبيع النص =======================
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "@": "a", "$": "s", "!": "i"})
_PREFIXES = ("وال", "بال", "فال", "لل", "ال", "يا", "و", "ف", "ب")


def simple_norm(text: str) -> str:
    """تطبيع خفيف للأوامر والردود."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = _INVISIBLE.sub("", text)
    text = _DIACRITICS.sub("", text)
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """تطبيع قوي لكشف الكلمات البذيئة."""
    text = unicodedata.normalize("NFKC", text).lower()
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
    tokens = re.findall(r"\w+", normalize(text))
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
                if len(w) >= 4 and w in c:
                    return w
    return None


# ======================= فحص الصور =======================
try:
    from nudenet import NudeDetector
    _detector = NudeDetector()
    log.info("NudeNet loaded ✅")
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
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "img")
        f = await context.bot.get_file(file_id)
        await f.download_to_drive(path)
        return await asyncio.to_thread(_scan_image, path)


# ======================= الرتب =======================
_tg_cache: dict[int, tuple[float, dict]] = {}


async def tg_statuses(chat_id, context) -> dict:
    cached = _tg_cache.get(chat_id)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    admins = await context.bot.get_chat_administrators(chat_id)
    m = {a.user.id: a.status for a in admins}
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


# ======================= أدوات =======================
async def send_temp(chat, text, seconds=NOTICE_DELETE_AFTER, **kw):
    m = await chat.send_message(text, parse_mode=ParseMode.HTML, **kw)
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


def time_text() -> str:
    n = datetime.now(TZ)
    h = n.hour % 12 or 12
    ampm = "صباحاً" if n.hour < 12 else "مساءً"
    return f"🕰 الساعة الحين: <b>{h}:{n.minute:02d} {ampm}</b>\n📍 بتوقيت مكة المكرمة"


def date_text() -> str:
    n = datetime.now(TZ)
    text = f"📅 اليوم: <b>{DAYS[n.weekday()]}</b>\n🗓 الميلادي: <b>{n.day} {G_MONTHS[n.month - 1]} {n.year}م</b>"
    try:
        try:
            from hijridate import Gregorian
        except ImportError:
            from hijri_converter import Gregorian
        h = Gregorian(n.year, n.month, n.day).to_hijri()
        text += f"\n🌙 الهجري: <b>{h.day} {H_MONTHS[h.month - 1]} {h.year}هـ</b>"
    except Exception as e:
        log.warning("hijri failed: %s", e)
    return text


# ======================= نظام المخالفات =======================
async def violation(update: Update, context, reason: str, chat=None, user=None):
    chat = chat or update.effective_chat
    user = user or update.effective_user
    cd = chat_data(chat.id)
    w = cd["warns"].setdefault(str(user.id), {"count": 0, "restricted": False})
    who = user.mention_html()
    try:
        if w["restricted"]:
            await context.bot.ban_chat_member(chat.id, user.id)
            cd["warns"].pop(str(user.id), None)
            text = (f"⛔ تم حظر {who} نهائياً\n"
                    f"📌 المخالفة: {reason}\n"
                    f"السبب: كرر المخالفة بعد ما تقيّد")
        else:
            w["count"] += 1
            if w["count"] >= MAX_WARNS:
                await mute(context, chat.id, user.id, RESTRICT_HOURS * 60)
                w["count"], w["restricted"] = 0, True
                text = (f"🔒 تم تقييد {who} لمدة يوم كامل\n"
                        f"📌 آخر مخالفة: {reason}\n"
                        f"السبب: وصل {MAX_WARNS} مخالفات\n\n"
                        f"⚠️ أي مخالفة بعد كذا = حظر نهائي")
            else:
                left = MAX_WARNS - w["count"]
                text = (f"⚠️ تحذير ({w['count']}/{MAX_WARNS}) — {who}\n"
                        f"🗑 انحذفت رسالتك\n"
                        f"📌 المخالفة: {reason}\n\n"
                        f"باقي لك {left} وتتقيد يوم كامل")
        mark_dirty()
        await send_temp(chat, text)
    except Exception as e:
        log.warning("violation failed: %s", e)


recent_msgs: dict[tuple[int, int], deque] = defaultdict(deque)


async def check_violation(update: Update, context, rank: int) -> str | None:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    cd = chat_data(chat.id)
    text = " ".join(filter(None, [msg.text, msg.caption]))
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    bad = words_for(chat.id)

    if find_bad_word(text, bad):
        return "كلام بذيء"
    if msg.poll and find_bad_word(" ".join([msg.poll.question] + [o.text for o in msg.poll.options]), bad):
        return "كلام بذيء"
    if rank < VIP:
        if cd["locks"].get("links", True) and (
            LINK_RE.search(text)
            or any(e.type in (MessageEntityType.URL, MessageEntityType.TEXT_LINK) for e in entities)
            or msg.reply_markup is not None
        ):
            return "إرسال روابط"
        if cd["locks"].get("forward", True) and (msg.forward_origin or msg.via_bot or msg.story):
            return "إعادة توجيه"
    if await is_nsfw(msg, context):
        return "محتوى إباحي"
    if rank < VIP and not update.edited_message:
        key, now = (chat.id, user.id), time.time()
        q = recent_msgs[key]
        q.append(now)
        while q and now - q[0] > FLOOD_WINDOW:
            q.popleft()
        if len(q) > FLOOD_LIMIT:
            q.clear()
            return "تكرار الرسائل (سبام)"
    return None


# ======================= قوائم الأوامر =======================
MEMBER_HELP = (
    "📋 <b>أوامر الأعضاء</b>\n\n"
    "• <code>الوقت</code> — الساعة الحين\n"
    "• <code>التاريخ</code> — التاريخ هجري وميلادي\n"
    "• <code>ايدي</code> — معلوماتك\n"
    "• <code>رتبتي</code> — رتبتك بالقروب\n"
    "• <code>رتبته</code> — (بالرد) رتبة شخص\n"
    "• <code>مخالفاتي</code> — عدد مخالفاتك\n"
    "• <code>الردود</code> — قائمة الردود\n"
    "• <code>المطور</code> — مطور البوت\n\n"
    f"⚠️ القوانين: {MAX_WARNS} مخالفات = تقييد يوم، وبعدها أي مخالفة = حظر"
)

HELP_SECTIONS = {
    "prot": (ADMIN, "🛡 <b>أوامر الحماية</b> (بالرد على رسالة الشخص)\n\n"
                    "• <code>حظر</code> / <code>الغاء الحظر</code>\n"
                    "• <code>كتم</code> أو <code>كتم 30</code> (بالدقائق) / <code>الغاء الكتم</code>\n"
                    "• <code>تقييد</code> — تقييد يوم / <code>الغاء التقييد</code>\n"
                    "• <code>انذار</code> — يعطيه مخالفة\n"
                    "• <code>المخالفات</code> — عدد مخالفاته\n"
                    "• <code>مسح المخالفات</code> — يصفّر مخالفاته"),
    "ranks": (ADMIN, "🎖 <b>أوامر الرتب</b> (بالرد)\n\n"
                     "• <code>رفع مميز</code> / <code>تنزيل مميز</code> — للأدمن وفوق\n"
                     "• <code>رفع ادمن</code> / <code>تنزيل ادمن</code> — للمدير وفوق\n"
                     "• <code>رفع مدير</code> / <code>تنزيل مدير</code> — للمالك\n"
                     "• <code>الرتب</code> — قائمة أصحاب الرتب\n"
                     "• <code>مسح الرتب</code> — للمالك\n\n"
                     "💎 المميز: يقدر يرسل روابط وتوجيه\n"
                     "⭐ الأدمن وفوق: ما يتطبق عليهم الفلتر"),
    "replies": (ADMIN, "💬 <b>الردود والكلمات</b>\n\n"
                       "• <code>اضف رد</code> — البوت يسألك عن الكلمة ثم الرد\n"
                       "• <code>مسح رد</code> — يسألك وش الكلمة\n"
                       "• <code>الردود</code> — قائمة الردود\n"
                       "• <code>مسح الردود</code> — للمدير وفوق\n\n"
                       "• <code>اضف كلمه</code> أو <code>اضف كلمه الكلمة</code> — منع كلمة\n"
                       "• <code>مسح كلمه</code> — فك منع كلمة\n"
                       "• <code>الكلمات الممنوعه</code> — القائمة\n\n"
                       "اكتب <code>الغاء</code> لإلغاء أي عملية"),
    "settings": (MANAGER, "⚙️ <b>الإعدادات</b> (للمدير وفوق)\n\n"
                          "• <code>قفل الروابط</code> / <code>فتح الروابط</code>\n"
                          "• <code>قفل التوجيه</code> / <code>فتح التوجيه</code>\n"
                          "• <code>قفل التحقق</code> / <code>فتح التحقق</code>\n"
                          "• <code>الاعدادات</code> — حالة الأقفال"),
}


def help_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 الحماية", callback_data="h:prot"),
         InlineKeyboardButton("🎖 الرتب", callback_data="h:ranks")],
        [InlineKeyboardButton("💬 الردود والكلمات", callback_data="h:replies"),
         InlineKeyboardButton("⚙️ الإعدادات", callback_data="h:settings")],
        [InlineKeyboardButton("👤 أوامر الأعضاء", callback_data="h:member")],
        [InlineKeyboardButton("👨‍💻 المطور", url=DEVELOPER_URL)],
    ])


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    section = q.data.split(":")[1]
    if section == "member":
        await q.answer()
        await q.edit_message_text(MEMBER_HELP, parse_mode=ParseMode.HTML, reply_markup=help_keyboard())
        return
    min_rank, text = HELP_SECTIONS[section]
    rank = await get_rank(q.message.chat.id, q.from_user.id, context)
    if rank < min_rank:
        await q.answer(f"هذا القسم لـ{RANK_NAMES[min_rank]} وفوق", show_alert=True)
        return
    await q.answer()
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=help_keyboard())


# ======================= تنفيذ الأوامر =======================
pending: dict[tuple[int, int], dict] = {}


class Ctx:
    def __init__(self, update, context, rank, arg):
        self.update, self.context, self.rank, self.arg = update, context, rank, arg
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
        await c.reply("↩️ استخدم الأمر بالرد على رسالة الشخص")
        return False
    c.target = r.from_user
    c.target_rank = await get_rank(c.chat.id, c.target.id, c.context)
    return True


async def _check_above(c: Ctx) -> bool:
    if c.target_rank >= c.rank:
        await c.reply(f"🚫 ما تقدر، رتبته {RANK_NAMES[c.target_rank]}")
        return False
    return True


# ---------- أوامر الأعضاء ----------
async def c_help(c: Ctx):
    if c.rank >= ADMIN:
        await c.reply(f"👋 أهلاً {RANK_NAMES[c.rank]}\nاختار القسم:", reply_markup=help_keyboard())
    else:
        await c.reply(MEMBER_HELP, reply_markup=DEV_BUTTON)


async def c_time(c: Ctx):
    await c.reply(time_text())


async def c_date(c: Ctx):
    await c.reply(date_text())


async def c_id(c: Ctx):
    u = c.user
    w = c.cd["warns"].get(str(u.id), {}).get("count", 0)
    await c.reply(
        f"👤 الاسم: {u.mention_html()}\n"
        f"🆔 الايدي: <code>{u.id}</code>\n"
        f"🏷 اليوزر: @{u.username or 'ما فيه'}\n"
        f"🎖 الرتبة: {RANK_NAMES[c.rank]}\n"
        f"⚠️ المخالفات: {w}/{MAX_WARNS}"
    )


async def c_myrank(c: Ctx):
    await c.reply(f"🎖 رتبتك: {RANK_NAMES[c.rank]}")


async def c_hisrank(c: Ctx):
    await c.reply(f"🎖 رتبة {c.target.mention_html()}: {RANK_NAMES[c.target_rank]}")


async def c_mywarns(c: Ctx):
    w = c.cd["warns"].get(str(c.user.id), {"count": 0, "restricted": False})
    extra = "\n🔴 انتبه: تقيّدت قبل، أي مخالفة = حظر" if w.get("restricted") else ""
    await c.reply(f"⚠️ مخالفاتك: {w['count']}/{MAX_WARNS}{extra}")


async def c_dev(c: Ctx):
    await c.reply(f"👨‍💻 مطور البوت: {DEVELOPER}", reply_markup=DEV_BUTTON)


async def c_replies(c: Ctx):
    reps = c.cd["replies"]
    if not reps:
        await c.reply("📭 ما فيه ردود مضافة\nالمشرفين يضيفون بأمر <code>اضف رد</code>")
        return
    lines = [f"{i}. {v['word']}" for i, v in enumerate(reps.values(), 1)]
    await c.reply("💬 <b>قائمة الردود:</b>\n\n" + "\n".join(lines))


# ---------- أوامر الحماية ----------
async def c_ban(c: Ctx):
    if not await _check_above(c):
        return
    await c.context.bot.ban_chat_member(c.chat.id, c.target.id)
    await c.reply(f"⛔ تم حظر {c.target.mention_html()}")


async def c_unban(c: Ctx):
    await c.context.bot.unban_chat_member(c.chat.id, c.target.id, only_if_banned=True)
    c.cd["warns"].pop(str(c.target.id), None)
    mark_dirty()
    await c.reply(f"✅ تم الغاء حظر {c.target.mention_html()}")


async def c_mute(c: Ctx):
    if not await _check_above(c):
        return
    minutes = int(c.arg) if c.arg.isdigit() else None
    await mute(c.context, c.chat.id, c.target.id, minutes)
    await c.reply(f"🔇 تم كتم {c.target.mention_html()} ({f'{minutes} دقيقة' if minutes else 'حتى يتفك'})")


async def c_restrict(c: Ctx):
    if not await _check_above(c):
        return
    await mute(c.context, c.chat.id, c.target.id, RESTRICT_HOURS * 60)
    await c.reply(f"🔒 تم تقييد {c.target.mention_html()} لمدة يوم")


async def c_unmute(c: Ctx):
    await unmute(c.context, c.chat.id, c.target.id)
    await c.reply(f"🔊 تم فك القيود عن {c.target.mention_html()}")


async def c_warn(c: Ctx):
    if not await _check_above(c):
        return
    await violation(c.update, c.context, c.arg or "إنذار من المشرف", chat=c.chat, user=c.target)


async def c_warns(c: Ctx):
    w = c.cd["warns"].get(str(c.target.id), {"count": 0, "restricted": False})
    extra = "\n🔴 تقيّد قبل — المخالفة الجاية حظر" if w.get("restricted") else ""
    await c.reply(f"⚠️ مخالفات {c.target.mention_html()}: {w['count']}/{MAX_WARNS}{extra}")


async def c_clear_warns(c: Ctx):
    c.cd["warns"].pop(str(c.target.id), None)
    mark_dirty()
    await c.reply(f"✅ تم مسح مخالفات {c.target.mention_html()}")


# ---------- أوامر الرتب ----------
def _make_promote(rank_value: int, up: bool):
    async def fn(c: Ctx):
        if not await _check_above(c):
            return
        tid = str(c.target.id)
        if up:
            c.cd["ranks"][tid] = KEY_OF[rank_value]
            msg = f"✅ تم رفع {c.target.mention_html()} إلى {RANK_NAMES[rank_value]}"
        else:
            if c.cd["ranks"].get(tid) != KEY_OF[rank_value]:
                await c.reply(f"ℹ️ {c.target.mention_html()} مو {RANK_NAMES[rank_value]}")
                return
            c.cd["ranks"].pop(tid)
            msg = f"⬇️ تم تنزيل {c.target.mention_html()} من {RANK_NAMES[rank_value]}"
        mark_dirty()
        await c.reply(msg)
    return fn


async def c_ranks_list(c: Ctx):
    groups = defaultdict(list)
    for uid, key in c.cd["ranks"].items():
        groups[RANK_KEYS[key]].append(uid)
    if not groups:
        await c.reply("📭 ما فيه رتب مضافة")
        return
    text = "🎖 <b>الرتب:</b>\n"
    for r in (MANAGER, ADMIN, VIP):
        if groups[r]:
            text += f"\n{RANK_NAMES[r]}:\n" + "\n".join(
                f"• <a href='tg://user?id={u}'>{u}</a>" for u in groups[r]
            ) + "\n"
    await c.reply(text)


async def c_clear_ranks(c: Ctx):
    c.cd["ranks"].clear()
    mark_dirty()
    await c.reply("✅ تم مسح كل الرتب")


# ---------- الردود والكلمات ----------
async def c_add_reply(c: Ctx):
    pending[(c.chat.id, c.user.id)] = {"step": "reply_word", "t": time.time()}
    await c.reply("📝 أرسل <b>الكلمة</b> اللي تبي البوت يرد عليها\n(أو <code>الغاء</code>)")


async def c_del_reply(c: Ctx):
    if c.arg:
        await _do_del_reply(c, c.arg)
        return
    pending[(c.chat.id, c.user.id)] = {"step": "del_reply", "t": time.time()}
    await c.reply("🗑 أرسل الكلمة اللي تبي تمسح ردها")


async def _do_del_reply(c: Ctx, word: str):
    if c.cd["replies"].pop(simple_norm(word), None):
        mark_dirty()
        await c.reply(f"✅ تم مسح الرد: {word}")
    else:
        await c.reply("❌ ما لقيت رد بهالكلمة")


async def c_clear_replies(c: Ctx):
    c.cd["replies"].clear()
    mark_dirty()
    await c.reply("✅ تم مسح كل الردود")


async def c_add_word(c: Ctx):
    if c.arg:
        await _do_add_word(c, c.arg)
        return
    pending[(c.chat.id, c.user.id)] = {"step": "add_word", "t": time.time()}
    await c.reply("🚫 أرسل الكلمة اللي تبي تمنعها")


async def _do_add_word(c: Ctx, word: str):
    try:
        await c.msg.delete()
    except Exception:
        pass
    if word in c.cd["unwords"]:
        c.cd["unwords"].remove(word)
    if word not in c.cd["words"]:
        c.cd["words"].append(word)
    mark_dirty()
    await c.chat.send_message("✅ تم إضافة الكلمة للممنوعات\nأي أحد يكتبها بتنحذف رسالته وياخذ مخالفة")


async def c_del_word(c: Ctx):
    if c.arg:
        await _do_del_word(c, c.arg)
        return
    pending[(c.chat.id, c.user.id)] = {"step": "del_word", "t": time.time()}
    await c.reply("✅ أرسل الكلمة اللي تبي تفك منعها")


async def _do_del_word(c: Ctx, word: str):
    if word in c.cd["words"]:
        c.cd["words"].remove(word)
    elif normalize(word) in GLOBAL_WORDS:
        c.cd["unwords"].append(word)
    else:
        await c.reply("❌ هالكلمة مو ممنوعة")
        return
    mark_dirty()
    await c.reply("✅ تم حذف الكلمة من الممنوعات")


async def c_list_words(c: Ctx):
    added = c.cd["words"]
    text = f"🚫 <b>الكلمات الممنوعة</b>\n\n📦 الأساسية: {len(GLOBAL_WORDS)} كلمة"
    if added:
        text += f"\n➕ المضافة ({len(added)}):\n<tg-spoiler>" + "، ".join(added) + "</tg-spoiler>"
    await c.reply(text)


# ---------- الإعدادات ----------
LOCK_NAMES = {"links": "الروابط", "forward": "التوجيه", "captcha": "التحقق"}


def _make_lock(key: str, value: bool):
    async def fn(c: Ctx):
        c.cd["locks"][key] = value
        mark_dirty()
        await c.reply(f"{'🔒 تم قفل' if value else '🔓 تم فتح'} {LOCK_NAMES[key]}")
    return fn


async def c_settings(c: Ctx):
    lines = [f"{'🔒' if c.cd['locks'].get(k, True) else '🔓'} {n}" for k, n in LOCK_NAMES.items()]
    await c.reply("⚙️ <b>الإعدادات:</b>\n\n" + "\n".join(lines) +
                  f"\n\n⚠️ المخالفات: {MAX_WARNS} ← تقييد {RESTRICT_HOURS} ساعة ← حظر")


# ---------- جدول الأوامر ----------
# الأمر: (أقل رتبة، الدالة، يحتاج رد على رسالة؟)
COMMANDS = {
    "الاوامر": (MEMBER, c_help, False),
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
    "كتم": (ADMIN, c_mute, True),
    "الغاء الكتم": (ADMIN, c_unmute, True),
    "تقييد": (ADMIN, c_restrict, True),
    "الغاء التقييد": (ADMIN, c_unmute, True),
    "انذار": (ADMIN, c_warn, True),
    "المخالفات": (ADMIN, c_warns, True),
    "مسح المخالفات": (ADMIN, c_clear_warns, True),

    "رفع مميز": (ADMIN, _make_promote(VIP, True), True),
    "تنزيل مميز": (ADMIN, _make_promote(VIP, False), True),
    "رفع ادمن": (MANAGER, _make_promote(ADMIN, True), True),
    "تنزيل ادمن": (MANAGER, _make_promote(ADMIN, False), True),
    "رفع مدير": (OWNER, _make_promote(MANAGER, True), True),
    "تنزيل مدير": (OWNER, _make_promote(MANAGER, False), True),
    "الرتب": (ADMIN, c_ranks_list, False),
    "مسح الرتب": (OWNER, c_clear_ranks, False),

    "اضف رد": (ADMIN, c_add_reply, False),
    "مسح رد": (ADMIN, c_del_reply, False),
    "مسح الردود": (MANAGER, c_clear_replies, False),
    "اضف كلمه": (ADMIN, c_add_word, False),
    "مسح كلمه": (ADMIN, c_del_word, False),
    "الكلمات الممنوعه": (ADMIN, c_list_words, False),

    "قفل الروابط": (MANAGER, _make_lock("links", True), False),
    "فتح الروابط": (MANAGER, _make_lock("links", False), False),
    "قفل التوجيه": (MANAGER, _make_lock("forward", True), False),
    "فتح التوجيه": (MANAGER, _make_lock("forward", False), False),
    "قفل التحقق": (MANAGER, _make_lock("captcha", True), False),
    "فتح التحقق": (MANAGER, _make_lock("captcha", False), False),
    "الاعدادات": (MANAGER, c_settings, False),
}
_CMD_ORDER = sorted(COMMANDS, key=len, reverse=True)


async def try_command(update: Update, context, rank: int) -> bool:
    msg = update.effective_message
    raw = (msg.text or "").strip()
    if not raw:
        return False
    t = simple_norm(raw)
    for key in _CMD_ORDER:
        if t == key or t.startswith(key + " "):
            min_rank, fn, needs_target = COMMANDS[key]
            if rank < min_rank:
                if t == key:
                    await msg.reply_text(f"🚫 هذا الأمر لـ{RANK_NAMES[min_rank]} وفوق")
                return t == key
            arg = " ".join(raw.split()[len(key.split()):])
            c = Ctx(update, context, rank, arg)
            if needs_target and not await _resolve_target(c):
                return True
            try:
                await fn(c)
            except Exception as e:
                log.warning("command %s failed: %s", key, e)
                await msg.reply_text("❌ ما قدرت أنفذ الأمر — تأكد إن البوت مشرف وعنده الصلاحيات")
            return True
    return False


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
        await c.reply("❎ تم الإلغاء")
        return True

    step = p["step"]
    if step == "reply_word":
        p.update(step="reply_text", word=text, t=time.time())
        await c.reply(f"👍 الكلمة: <b>{text}</b>\nالحين أرسل <b>الرد</b>")
    elif step == "reply_text":
        pending.pop(key, None)
        c.cd["replies"][simple_norm(p["word"])] = {"word": p["word"], "reply": msg.text_html}
        mark_dirty()
        await c.reply(f"✅ تم إضافة الرد\n\n💬 الكلمة: {p['word']}\n↩️ الرد: {msg.text_html}")
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


async def try_auto_reply(update: Update, context) -> bool:
    msg, chat = update.effective_message, update.effective_chat
    if not msg.text or update.edited_message:
        return False
    t = simple_norm(msg.text)
    custom = chat_data(chat.id)["replies"].get(t)
    if custom:
        await msg.reply_text(custom["reply"], parse_mode=ParseMode.HTML)
        return True
    for trigger, answer in GREETINGS:
        if t == trigger or (len(trigger) > 4 and t.startswith(trigger)):
            await msg.reply_text(answer)
            return True
    return False


# ======================= معالج الرسائل الرئيسي =======================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    if not msg or not user:
        return
    rank = await sender_rank(update, context)

    if rank < ADMIN:
        reason = await check_violation(update, context, rank)
        if reason:
            try:
                await msg.delete()
            except Exception as e:
                log.warning("delete failed: %s", e)
            await violation(update, context, reason)
            return

    if update.edited_message:
        return
    if rank >= ADMIN and await try_pending(update, context, rank):
        return
    if await try_command(update, context, rank):
        return
    await try_auto_reply(update, context)


# ======================= الأعضاء الجدد + التحقق =======================
pending_captcha: dict[tuple[int, int], int] = {}


async def new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat = update.effective_message, update.effective_chat
    adder_rank = await sender_rank(update, context)
    cd = chat_data(chat.id)
    try:
        await msg.delete()
    except Exception:
        pass

    for m in msg.new_chat_members:
        if m.id == context.bot.id:
            await chat.send_message(
                "🛡 أهلاً! أنا بوت الحماية\n\n"
                "ارفعني مشرف بصلاحيات: حذف الرسائل + حظر الأعضاء\n"
                "واكتب <code>الاوامر</code> عشان تشوف كل شي",
                parse_mode=ParseMode.HTML,
                reply_markup=DEV_BUTTON,
            )
            continue
        if m.is_bot:
            if adder_rank < ADMIN:
                await context.bot.ban_chat_member(chat.id, m.id)
            continue
        if find_bad_word(m.full_name, words_for(chat.id)) or LINK_RE.search(m.full_name or ""):
            await context.bot.ban_chat_member(chat.id, m.id)
            await send_temp(chat, "⛔ انطرد عضو جديد لأن اسمه فيه كلام ممنوع")
            continue
        if adder_rank >= ADMIN or not cd["locks"].get("captcha", True):
            continue
        try:
            await mute(context, chat.id, m.id)
        except Exception as e:
            log.warning("captcha restrict failed: %s", e)
            continue
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ أنا مو بوت", callback_data=f"v:{m.id}")]])
        cap = await chat.send_message(
            f"أهلاً {m.mention_html()} 👋\n"
            f"اضغط الزر خلال {CAPTCHA_SECONDS // 60} دقائق عشان تقدر تكتب\n\n"
            f"📌 القوانين: ممنوع الكلام البذيء والروابط\n"
            f"{MAX_WARNS} مخالفات = تقييد يوم، وبعدها حظر",
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
        pending_captcha[(chat.id, m.id)] = cap.message_id
        asyncio.get_running_loop().create_task(captcha_timeout(context, chat.id, m.id, cap))


async def captcha_timeout(context, chat_id, user_id, cap_msg):
    await asyncio.sleep(CAPTCHA_SECONDS)
    if pending_captcha.pop((chat_id, user_id), None) is None:
        return
    try:
        await context.bot.ban_chat_member(chat_id, user_id)
        await context.bot.unban_chat_member(chat_id, user_id)
        await cap_msg.delete()
    except Exception as e:
        log.warning("captcha kick failed: %s", e)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    target = int(q.data.split(":")[1])
    if q.from_user.id != target:
        await q.answer("هذا الزر مو لك 🙂", show_alert=True)
        return
    chat_id = q.message.chat.id
    pending_captcha.pop((chat_id, target), None)
    await unmute(context, chat_id, target)
    await q.answer("تم التحقق ✅ حياك")
    try:
        await q.message.delete()
    except Exception:
        pass


# ======================= الخاص =======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        rank = await sender_rank(update, context)
        await c_help(Ctx(update, context, rank, ""))
        return
    me = await context.bot.get_me()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ضيفني لقروبك", url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("👨‍💻 المطور", url=DEVELOPER_URL)],
    ])
    await update.effective_message.reply_text(
        "🛡 <b>بوت حماية القروبات</b>\n\n"
        "• يحذف الكلام البذيء والصور الإباحية\n"
        "• يمنع الروابط والتوجيه والسبام\n"
        f"• نظام مخالفات: {MAX_WARNS} تحذيرات ← تقييد يوم ← حظر\n"
        "• رتب: مالك، مدير، أدمن، مميز\n"
        "• ردود تلقائية وردود تضيفها بنفسك\n"
        "• الوقت والتاريخ هجري وميلادي\n\n"
        "ضيفني لقروبك وارفعني مشرف، بعدين اكتب <code>الاوامر</code>\n\n"
        f"👨‍💻 المطور: {DEVELOPER}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = simple_norm(update.effective_message.text or "")
    if t in ("الوقت", "الساعه"):
        await update.effective_message.reply_text(time_text(), parse_mode=ParseMode.HTML)
    elif t == "التاريخ":
        await update.effective_message.reply_text(date_text(), parse_mode=ParseMode.HTML)
    elif t == "المطور":
        await update.effective_message.reply_text(f"👨‍💻 المطور: {DEVELOPER}", reply_markup=DEV_BUTTON)
    else:
        for trigger, answer in GREETINGS:
            if t == trigger or (len(trigger) > 4 and t.startswith(trigger)):
                await update.effective_message.reply_text(answer)
                return


# ======================= التشغيل =======================
async def post_init(app: Application):
    asyncio.get_running_loop().create_task(saver_loop())


async def post_shutdown(app: Application):
    write_data()
    if GIT_SAVE:
        git_push()
    log.info("Data saved ✅")


def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    groups = filters.ChatType.GROUPS

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(verify, pattern=r"^v:\d+$"))
    app.add_handler(CallbackQueryHandler(help_callback, pattern=r"^h:\w+$"))
    app.add_handler(MessageHandler(groups & filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members))
    app.add_handler(MessageHandler(groups & ~filters.COMMAND & ~filters.StatusUpdate.ALL, on_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_text))

    log.info("Bot running — %d global words, %d chats", len(GLOBAL_WORDS), len(DATA.get("chats", {})))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
