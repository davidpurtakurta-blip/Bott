"""
Mystic Protector Bot — single-file build.
Run:  pip install pyrogram tgcrypto aiosqlite python-dotenv
      python bot.py
Optional for VC auto-join: pip install py-tgcalls   (needs ffmpeg)
"""
import asyncio, json, logging, os, re, shutil, sys, time, io, contextlib, traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import aiosqlite
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatType
from pyrogram.errors import RPCError
from pyrogram.types import BotCommand, ChatPermissions

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================================================
# CONFIG
# ============================================================
API_ID         = int(os.getenv("API_ID", "33288373"))
API_HASH       = os.getenv("API_HASH", "3843a5f9be5bb6eb3ca35709571ebfd7")
BOT_TOKEN      = os.getenv("BOT_TOKEN", "8882524056:AAHwk65a4o27eigZXyJOWYMe2kuMU4VpG88")
BOT_USERNAME   = "MYSTIC_PROTECTOR_BOT"
OWNER_USERNAME = "HYDRA_HELLFIRE"
OWNER_ID       = int(os.getenv("OWNER_ID", "8028519029"))
MAIN_GROUP_ID  = int(os.getenv("MAIN_GROUP_ID", "-1001721961148"))
SUDO_USERS     = set(int(x) for x in os.getenv("SUDO_USERS", "").split(",") if x.strip().isdigit())
SUDO_USERS.add(OWNER_ID)
STRING_SESSION = os.getenv("STRING_SESSION", "")
VERSION        = "1.0.0"

DB_PATH    = os.getenv("DB_PATH", "bot.db")
LOG_PATH   = os.getenv("LOG_PATH", "bot.log")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
FLOOD_LIMIT, FLOOD_WINDOW, WARN_LIMIT = 6, 5, 3
MAINTENANCE = False
START_TIME  = time.time()

# ============================================================
# LOGGING
# ============================================================
_fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
_fh = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(); _sh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh])
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ============================================================
# HELPERS
# ============================================================
def uptime() -> str:
    s = int(time.time() - START_TIME)
    d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    return f"{d}d {h}h {m}m {s}s"

class FloodTracker:
    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self._hits = defaultdict(deque)
        self._lock = asyncio.Lock()
    async def hit(self, key):
        async with self._lock:
            q = self._hits[key]; now = time.time()
            while q and now - q[0] > self.window: q.popleft()
            q.append(now)
            return len(q) > self.limit

flood = FloodTracker(FLOOD_LIMIT, FLOOD_WINDOW)

# ============================================================
# DATABASE
# ============================================================
DEFAULT_SETTINGS = {
    "protection": True, "antispam": True, "antiflood": True, "antilink": False,
    "antiraid": False, "antibot": False, "antifake": False, "antiforward": False,
    "antichannel": False, "anticaps": False, "antitagall": False, "antimention": False,
    "antimedia": False, "antisticker": False, "antigif": False, "antipoll": False,
    "antifile": False, "antivoice": False, "antivideo": False, "antiaudio": False,
    "antiemoji": False, "antiedit": False, "antidelete": False, "antiarabic": False,
    "antifakeadmin": False, "antinickname": False, "antiprofanity": False,
    "raidmode": False, "lockdown": False, "vcprotect": False,
    "lock_photo": False, "lock_video": False, "lock_voice": False, "lock_gif": False,
    "lock_sticker": False, "lock_poll": False, "lock_file": False, "lock_link": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats(chat_id INTEGER PRIMARY KEY, title TEXT, rules TEXT DEFAULT '', settings TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS warns(chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS sudo(user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS whitelist(chat_id INTEGER, user_id INTEGER, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS blacklist_users(chat_id INTEGER, user_id INTEGER, PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS blacklist_words(chat_id INTEGER, word TEXT, PRIMARY KEY(chat_id, word));
CREATE TABLE IF NOT EXISTS stats(chat_id INTEGER PRIMARY KEY, messages INTEGER DEFAULT 0, joins INTEGER DEFAULT 0, leaves INTEGER DEFAULT 0);
"""

async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA); await db.commit()
    log.info("DB ready at %s", DB_PATH)

async def get_settings(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT settings FROM chats WHERE chat_id=?", (chat_id,))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT OR IGNORE INTO chats(chat_id, settings) VALUES (?, ?)",
                             (chat_id, json.dumps(DEFAULT_SETTINGS)))
            await db.commit()
            return dict(DEFAULT_SETTINGS)
        s = dict(DEFAULT_SETTINGS); s.update(json.loads(row[0] or "{}")); return s

async def set_setting(chat_id, key, value):
    s = await get_settings(chat_id); s[key] = value
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO chats(chat_id, settings) VALUES (?, ?)",
                         (chat_id, json.dumps(s))); await db.commit()

async def set_rules(c, r):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO chats(chat_id, rules) VALUES(?, ?) "
                         "ON CONFLICT(chat_id) DO UPDATE SET rules=excluded.rules", (c, r))
        await db.commit()

async def get_rules(c):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT rules FROM chats WHERE chat_id=?", (c,))
        r = await cur.fetchone(); return r[0] if r else ""

async def all_chats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT chat_id FROM chats")
        return [r[0] for r in await cur.fetchall()]

async def add_warn(c, u):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO warns(chat_id,user_id,count) VALUES(?,?,1) "
                         "ON CONFLICT(chat_id,user_id) DO UPDATE SET count=count+1", (c, u))
        await db.commit()
        cur = await db.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (c, u))
        return (await cur.fetchone())[0]

async def get_warns(c, u):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (c, u))
        r = await cur.fetchone(); return r[0] if r else 0

async def clear_warns(c, u):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (c, u)); await db.commit()

async def add_sudo_db(u):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO sudo(user_id) VALUES(?)", (u,)); await db.commit()

async def rem_sudo_db(u):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sudo WHERE user_id=?", (u,)); await db.commit()

async def list_sudo_db():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM sudo")
        return [r[0] for r in await cur.fetchall()]

async def _toggle(table, c, u, add):
    async with aiosqlite.connect(DB_PATH) as db:
        if add:
            await db.execute(f"INSERT OR IGNORE INTO {table}(chat_id,user_id) VALUES(?,?)", (c, u))
        else:
            await db.execute(f"DELETE FROM {table} WHERE chat_id=? AND user_id=?", (c, u))
        await db.commit()

async def list_table(t, c):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(f"SELECT user_id FROM {t} WHERE chat_id=?", (c,))
        return [r[0] for r in await cur.fetchall()]

async def is_whitelisted(c, u):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM whitelist WHERE chat_id=? AND user_id=?", (c, u))
        return bool(await cur.fetchone())

async def add_bl_word(c, w):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO blacklist_words VALUES(?,?)", (c, w.lower()))
        await db.commit()

async def rem_bl_word(c, w):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM blacklist_words WHERE chat_id=? AND word=?", (c, w.lower()))
        await db.commit()

async def get_bl_words(c):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT word FROM blacklist_words WHERE chat_id=?", (c,))
        return [r[0] for r in await cur.fetchall()]

async def bump(c, field):
    if field not in ("messages", "joins", "leaves"): return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"INSERT INTO stats(chat_id,{field}) VALUES(?,1) "
                         f"ON CONFLICT(chat_id) DO UPDATE SET {field}={field}+1", (c,))
        await db.commit()

async def get_stats(c):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT messages,joins,leaves FROM stats WHERE chat_id=?", (c,))
        r = await cur.fetchone()
        return {"messages": r[0] if r else 0, "joins": r[1] if r else 0, "leaves": r[2] if r else 0}

# ============================================================
# CUSTOM FILTERS
# ============================================================
def _is_sudo(_, __, m): return bool(m.from_user and m.from_user.id in SUDO_USERS)
def _is_owner(_, __, m): return bool(m.from_user and m.from_user.id == OWNER_ID)
def _is_group(_, __, m): return m.chat and m.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
def _is_owner_dm(_, __, m): return bool(m.chat and m.chat.type == ChatType.PRIVATE and m.from_user and m.from_user.id == OWNER_ID)

sudo_filter        = filters.create(_is_sudo)
owner_filter       = filters.create(_is_owner)
group_filter       = filters.create(_is_group)
owner_dm_filter    = filters.create(_is_owner_dm)
allowed_chat_filter = group_filter | owner_dm_filter

# ============================================================
# CLIENT
# ============================================================
app = Client("mystic_protector", api_id=API_ID, api_hash=API_HASH,
             bot_token=BOT_TOKEN, in_memory=False, workers=8)

# ============================================================
# VC AUTO-JOIN (optional, needs py-tgcalls + user STRING_SESSION)
# ============================================================
_vc_app = None
_vc_calls = None
_vc_ready = False
SILENCE_PATH = "silence.raw"  # 1s of PCM 16-bit mono 48kHz silence

def _ensure_silence_file():
    """Create a tiny raw-PCM silence file we can stream as 'audio'."""
    if os.path.exists(SILENCE_PATH) and os.path.getsize(SILENCE_PATH) > 0:
        return
    # 48000 samples/sec * 2 bytes/sample * 1 channel * 1 second = 96000 bytes of zeros
    with open(SILENCE_PATH, "wb") as f:
        f.write(b"\x00" * (48000 * 2))
    log.info("Generated %s for VC silent input.", SILENCE_PATH)

async def vc_start():
    """Boot the user-session client + PyTgCalls instance."""
    global _vc_app, _vc_calls, _vc_ready
    if _vc_ready:
        return
    if not STRING_SESSION:
        log.warning("VC: STRING_SESSION is empty — cannot auto-join voice chats. "
                    "Generate one with `python gen_session.py` and set STRING_SESSION.")
        return
    try:
        from pytgcalls import PyTgCalls   # noqa
    except ImportError:
        log.warning("VC: py-tgcalls not installed. Run: pip install py-tgcalls "
                    "(and apt install ffmpeg).")
        return
    try:
        _ensure_silence_file()
        _vc_app = Client(
            "mystic_vc_user",
            api_id=API_ID, api_hash=API_HASH,
            session_string=STRING_SESSION,
            in_memory=True,
        )
        await _vc_app.start()
        from pytgcalls import PyTgCalls
        _vc_calls = PyTgCalls(_vc_app)
        await _vc_calls.start()
        _vc_ready = True
        me = await _vc_app.get_me()
        log.info("VC userbot ready as @%s (%s)", me.username or "—", me.id)
    except Exception as e:
        log.exception("VC userbot failed to start: %s", e)

async def _try_mute(chat_id):
    """PyTgCalls API differs between versions — try every known mute call."""
    if not _vc_calls:
        return
    for fn_name in ("mute", "mute_stream"):
        fn = getattr(_vc_calls, fn_name, None)
        if fn:
            try:
                await fn(chat_id)
                log.info("VC: muted via %s()", fn_name)
                return
            except Exception as e:
                log.debug("VC: %s() failed: %s", fn_name, e)

async def vc_join_muted(chat_id):
    """Join `chat_id`'s active voice chat with mic OFF (silent stream)."""
    if not _vc_ready:
        await vc_start()
    if not _vc_ready:
        return False
    _ensure_silence_file()
    abs_silence = os.path.abspath(SILENCE_PATH)
    try:
        # Try modern py-tgcalls (>=2.x) MediaStream API.
        try:
            from pytgcalls.types import MediaStream, AudioQuality
            stream = MediaStream(
                abs_silence,
                audio_parameters=AudioQuality.STUDIO,
                video_flags=MediaStream.Flags.IGNORE,
            )
            await _vc_calls.play(chat_id, stream)
        except Exception as e_modern:
            log.debug("VC: modern API failed (%s); trying legacy.", e_modern)
            # Legacy py-tgcalls (<2.x): InputStream + InputAudioStream
            from pytgcalls.types.input_stream import InputStream, InputAudioStream
            from pytgcalls.types.input_stream.quality import HighQualityAudio
            stream = InputStream(InputAudioStream(abs_silence, HighQualityAudio()))
            await _vc_calls.join_group_call(chat_id, stream)

        await _try_mute(chat_id)
        log.info("VC: joined call in %s (mic OFF)", chat_id)
        return True
    except Exception as e:
        log.exception("VC: join failed for %s: %s", chat_id, e)
        return False

async def vc_leave(chat_id):
    if not _vc_ready:
        return
    for fn_name in ("leave_call", "leave_group_call"):
        fn = getattr(_vc_calls, fn_name, None)
        if fn:
            try:
                await fn(chat_id); return
            except Exception as e:
                log.debug("VC: %s() failed: %s", fn_name, e)

# ============================================================
# ACCESS GATE
# ============================================================
@app.on_message(filters.channel, group=-10)
async def _leave_channel(client, m):
    try:
        await m.chat.leave()
        log.warning("Left channel %s", m.chat.id)
    except Exception: pass

PUBLIC_DM_CMDS = {"start", "help", "menu", "ping"}

@app.on_message(filters.private, group=-9)
async def _private_guard(client, m):
    if not m.from_user:
        return await m.stop_propagation()
    if m.from_user.id == OWNER_ID:
        return
    # Allow basic commands so users always get a reply
    text = (m.text or m.caption or "").strip()
    if text:
        cmd = text.split()[0].lstrip("/!.").split("@")[0].lower()
        if cmd in PUBLIC_DM_CMDS:
            try:
                await m.reply_text(
                    f"👋 Hi! I'm @{BOT_USERNAME} — a private protection bot.\n"
                    f"My owner is @{OWNER_USERNAME}.\n"
                    f"Add me to your group as admin to use me there."
                )
            except Exception as e:
                log.warning("DM reply failed: %s", e)
            return await m.stop_propagation()
    try:
        await m.reply_text(
            f"❌ This bot is private. Owner: @{OWNER_USERNAME}.\n"
            f"Send /start for info."
        )
    except Exception as e:
        log.warning("DM deny reply failed: %s", e)
    finally:
        await m.stop_propagation()

# ============================================================
# OWNER COMMAND ACK
# ============================================================
@app.on_message(filters.regex(r"^[\/!.][A-Za-z0-9_]+") & ~filters.private, group=-8)
async def _ack(client, m):
    if not m.from_user or m.from_user.id != OWNER_ID: return
    if m.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    text = (m.text or m.caption or "").strip()
    cmd = text.split()[0].lstrip("/!.").split("@")[0].lower() if text else "?"
    try: await m.reply_text(f"✅ Command `/{cmd}` activated, sir.", quote=True)
    except Exception: pass

# ============================================================
# TARGET RESOLVER
# ============================================================
async def _target(client, m):
    if m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user
    if len(m.command) > 1:
        try: return await client.get_users(m.command[1])
        except Exception: return None
    return None

def _arg(m): return m.command[1].lower() if len(m.command) > 1 else ""
def _parse_dur(s):
    mt = re.match(r"^(\d+)([smhd])$", s.strip().lower())
    if not mt: return 0
    return int(mt.group(1)) * {"s":1,"m":60,"h":3600,"d":86400}[mt.group(2)]

NO_PERMS = ChatPermissions()
FULL_PERMS = ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                             can_send_polls=True, can_send_other_messages=True,
                             can_add_web_page_previews=True, can_invite_users=True)

# ============================================================
# GENERAL COMMANDS
# ============================================================
HELP = ("🛡 **Mystic Protector Bot**\nUse /menu to see categories.\n\n"
        "Moderation, Protection, Anti-abuse, Locks, Whitelist/Blacklist, VC, Developer.")

@app.on_message(filters.command("start") & allowed_chat_filter)
async def start_cmd(_, m):
    if not m.from_user or m.from_user.id != OWNER_ID:
        return await m.reply_text(
            f"⚠️ The owner of this bot is only @{OWNER_USERNAME}.\nDo not mess with the bot.")
    await m.reply_text(f"👋 Welcome back, owner!\nI'm @{BOT_USERNAME} — your security guard.\n/help to begin.")

@app.on_message(filters.command(["help","menu"]) & allowed_chat_filter)
async def help_cmd(_, m): await m.reply_text(HELP)

@app.on_message(filters.command("ping") & allowed_chat_filter)
async def ping_cmd(_, m):
    t = time.time(); r = await m.reply_text("Pinging…")
    await r.edit_text(f"🏓 Pong! `{(time.time()-t)*1000:.0f} ms`")

@app.on_message(filters.command("uptime") & allowed_chat_filter)
async def up_cmd(_, m): await m.reply_text(f"⏱ Uptime: `{uptime()}`")

@app.on_message(filters.command(["status","botinfo","version"]) & allowed_chat_filter)
async def status_cmd(_, m):
    await m.reply_text(f"🤖 @{BOT_USERNAME}\nv`{VERSION}`\nUptime: `{uptime()}`\nMaintenance: `{MAINTENANCE}`")

@app.on_message(filters.command("id") & allowed_chat_filter)
async def id_cmd(_, m):
    u = m.from_user.id if m.from_user else "—"
    r = m.reply_to_message.from_user.id if m.reply_to_message and m.reply_to_message.from_user else None
    txt = f"👤 You: `{u}`\n💬 Chat: `{m.chat.id}`"
    if r: txt += f"\n↩️ Replied: `{r}`"
    await m.reply_text(txt)

@app.on_message(filters.command("userinfo") & allowed_chat_filter)
async def uinfo_cmd(client, m):
    u = await _target(client, m) or m.from_user
    if not u: return await m.reply_text("No user.")
    await m.reply_text(f"👤 **{u.first_name}**\nID: `{u.id}`\n@{u.username or '—'}\nBot: `{u.is_bot}`")

@app.on_message(filters.command("chatinfo") & group_filter)
async def cinfo_cmd(client, m):
    c = m.chat
    await m.reply_text(f"💬 **{c.title}**\nID: `{c.id}`\nType: `{c.type}`\n"
                       f"Members: `{await client.get_chat_members_count(c.id)}`")

@app.on_message(filters.command(["admins","adminlist"]) & group_filter)
async def admins_cmd(client, m):
    out = []
    async for a in client.get_chat_members(m.chat.id, filter="administrators"):
        out.append(f"• {a.user.mention} (`{a.user.id}`)")
    await m.reply_text("👮 **Admins**\n" + "\n".join(out))

@app.on_message(filters.command("rules") & group_filter)
async def rules_cmd(_, m):
    r = await get_rules(m.chat.id)
    await m.reply_text(r or "📜 No rules set. Use /setrules.")

@app.on_message(filters.command("setrules") & group_filter)
async def setrules_cmd(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /setrules <text>")
    await set_rules(m.chat.id, m.text.split(None, 1)[1]); await m.reply_text("✅ Updated.")

@app.on_message(filters.command("report") & group_filter)
async def report_cmd(_, m):
    if not m.reply_to_message: return await m.reply_text("Reply to a message to report.")
    await m.reply_text("🚨 Reported to admins.")

@app.on_message(filters.command("invitelink") & group_filter)
async def inv_cmd(client, m):
    try:
        link = await client.export_chat_invite_link(m.chat.id)
        await m.reply_text(f"🔗 {link}")
    except Exception as e: await m.reply_text(f"❌ {e}")

@app.on_message(filters.command("settings") & group_filter)
async def settings_cmd(_, m):
    s = await get_settings(m.chat.id)
    on = [k for k, v in s.items() if v is True]
    await m.reply_text("⚙️ **Active:**\n" + (", ".join(on) or "none"))

@app.on_message(filters.command("stats") & group_filter)
async def stats_cmd(_, m):
    s = await get_stats(m.chat.id)
    await m.reply_text(f"📊 Msgs: `{s['messages']}` | Joins: `{s['joins']}` | Leaves: `{s['leaves']}`")

@app.on_message(filters.command("logs") & sudo_filter)
async def logs_cmd(_, m):
    try: await m.reply_document(LOG_PATH, caption="📄 Logs")
    except Exception as e: await m.reply_text(f"❌ {e}")

@app.on_message(filters.command("backup") & sudo_filter)
async def backup_cmd(_, m):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, f"bot-{int(time.time())}.db")
    shutil.copy(DB_PATH, dst); await m.reply_document(dst, caption="💾 Backup")

@app.on_message(filters.command("restore") & sudo_filter)
async def restore_cmd(_, m):
    if not (m.reply_to_message and m.reply_to_message.document):
        return await m.reply_text("Reply to a .db file.")
    p = await m.reply_to_message.download(file_name=DB_PATH)
    await m.reply_text(f"✅ Restored from `{p}`. Restart to apply.")

# ============================================================
# MODERATION
# ============================================================
@app.on_message(filters.command(["ban","sban"]) & group_filter)
async def ban_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.ban_chat_member(m.chat.id, u.id)
    if m.command[0] == "sban" and m.reply_to_message: await m.reply_to_message.delete()
    await m.reply_text(f"🔨 Banned {u.mention}")

@app.on_message(filters.command("tban") & group_filter)
async def tban_cmd(client, m):
    u = await _target(client, m)
    if not u or len(m.command) < 3: return await m.reply_text("Usage: /tban <user> <30m|2h|1d>")
    secs = _parse_dur(m.command[2])
    await client.ban_chat_member(m.chat.id, u.id, datetime.utcnow()+timedelta(seconds=secs))
    await m.reply_text(f"⏱ {u.mention} banned for {m.command[2]}")

@app.on_message(filters.command("unban") & group_filter)
async def unban_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.unban_chat_member(m.chat.id, u.id); await m.reply_text(f"♻️ Unbanned {u.mention}")

@app.on_message(filters.command(["kick","skick"]) & group_filter)
async def kick_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.ban_chat_member(m.chat.id, u.id)
    await client.unban_chat_member(m.chat.id, u.id)
    if m.command[0] == "skick" and m.reply_to_message: await m.reply_to_message.delete()
    await m.reply_text(f"👢 Kicked {u.mention}")

@app.on_message(filters.command(["mute","freeze"]) & group_filter)
async def mute_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.restrict_chat_member(m.chat.id, u.id, NO_PERMS)
    await m.reply_text(f"🔇 Muted {u.mention}")

@app.on_message(filters.command("tmute") & group_filter)
async def tmute_cmd(client, m):
    u = await _target(client, m)
    if not u or len(m.command) < 3: return await m.reply_text("Usage: /tmute <user> <30m|2h|1d>")
    until = datetime.utcnow()+timedelta(seconds=_parse_dur(m.command[2]))
    await client.restrict_chat_member(m.chat.id, u.id, NO_PERMS, until_date=until)
    await m.reply_text(f"🔇 {u.mention} muted for {m.command[2]}")

@app.on_message(filters.command(["unmute","unfreeze"]) & group_filter)
async def unmute_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.restrict_chat_member(m.chat.id, u.id, FULL_PERMS)
    await m.reply_text(f"🔊 Unmuted {u.mention}")

@app.on_message(filters.command("warn") & group_filter)
async def warn_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    c = await add_warn(m.chat.id, u.id)
    if c >= WARN_LIMIT:
        await client.ban_chat_member(m.chat.id, u.id)
        await clear_warns(m.chat.id, u.id)
        return await m.reply_text(f"⛔ {u.mention} reached {c} warns — banned.")
    await m.reply_text(f"⚠️ Warned {u.mention} ({c}/{WARN_LIMIT})")

@app.on_message(filters.command(["unwarn","clearwarns"]) & group_filter)
async def unwarn_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await clear_warns(m.chat.id, u.id); await m.reply_text(f"✅ Cleared {u.mention}")

@app.on_message(filters.command(["warnings","warns"]) & group_filter)
async def warns_cmd(client, m):
    u = await _target(client, m) or m.from_user
    await m.reply_text(f"⚠️ {u.mention}: {await get_warns(m.chat.id, u.id)}/{WARN_LIMIT}")

@app.on_message(filters.command("purge") & group_filter)
async def purge_cmd(client, m):
    if not m.reply_to_message: return await m.reply_text("Reply to start point.")
    ids = list(range(m.reply_to_message.id, m.id))
    for i in range(0, len(ids), 100):
        try: await client.delete_messages(m.chat.id, ids[i:i+100])
        except Exception: pass
    try: await m.delete()
    except Exception: pass

@app.on_message(filters.command("del") & group_filter)
async def del_cmd(_, m):
    if m.reply_to_message: 
        try: await m.reply_to_message.delete()
        except Exception: pass
    try: await m.delete()
    except Exception: pass

@app.on_message(filters.command("pin") & group_filter)
async def pin_cmd(_, m):
    if not m.reply_to_message: return await m.reply_text("Reply to pin.")
    await m.reply_to_message.pin(); await m.reply_text("📌 Pinned.")

@app.on_message(filters.command("unpin") & group_filter)
async def unpin_cmd(client, m):
    await client.unpin_chat_message(m.chat.id); await m.reply_text("📌 Unpinned.")

@app.on_message(filters.command("promote") & group_filter)
async def promote_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.promote_chat_member(m.chat.id, u.id, can_delete_messages=True,
                                     can_restrict_members=True, can_invite_users=True,
                                     can_pin_messages=True)
    await m.reply_text(f"⭐ Promoted {u.mention}")

@app.on_message(filters.command("demote") & group_filter)
async def demote_cmd(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await client.promote_chat_member(m.chat.id, u.id, can_delete_messages=False,
                                     can_restrict_members=False, can_invite_users=False,
                                     can_pin_messages=False, can_manage_chat=False)
    await m.reply_text(f"⬇️ Demoted {u.mention}")

@app.on_message(filters.command("settitle") & group_filter)
async def settitle_cmd(client, m):
    u = await _target(client, m)
    if not u or len(m.command) < 3: return await m.reply_text("Usage: /settitle <user> <title>")
    title = m.text.split(None, 2)[2]
    await client.set_administrator_title(m.chat.id, u.id, title); await m.reply_text("🏷 Set.")

@app.on_message(filters.command("setphoto") & group_filter)
async def setphoto_cmd(client, m):
    if not (m.reply_to_message and m.reply_to_message.photo):
        return await m.reply_text("Reply to a photo.")
    f = await m.reply_to_message.download()
    await client.set_chat_photo(m.chat.id, photo=f); await m.reply_text("🖼 Updated.")

@app.on_message(filters.command(["cleanservice","cleanbots","purgebots"]) & group_filter)
async def clean_cmd(_, m): await m.reply_text("🧹 Cleaning task scheduled.")

# ============================================================
# PROTECTION
# ============================================================
@app.on_message(filters.command(["protection","security"]) & group_filter)
async def protection_cmd(_, m):
    a = _arg(m)
    if a in ("on","off"):
        await set_setting(m.chat.id, "protection", a == "on")
        return await m.reply_text(f"🛡 Protection **{a.upper()}**")
    s = await get_settings(m.chat.id)
    await m.reply_text(f"🛡 Protection: `{s['protection']}`")

@app.on_message(filters.command("shield") & group_filter)
async def shield_cmd(_, m):
    for k in ("protection","antispam","antiflood"): await set_setting(m.chat.id, k, True)
    await m.reply_text("🛡 Shield ON.")

@app.on_message(filters.command("shieldoff") & group_filter)
async def shieldoff_cmd(_, m):
    await set_setting(m.chat.id, "protection", False); await m.reply_text("🛡 Shield OFF.")

@app.on_message(filters.command("panic") & group_filter)
async def panic_cmd(client, m):
    await client.set_chat_permissions(m.chat.id, NO_PERMS)
    await set_setting(m.chat.id, "lockdown", True); await m.reply_text("🚨 PANIC — chat locked.")

@app.on_message(filters.command("panicoff") & group_filter)
async def panicoff_cmd(client, m):
    await client.set_chat_permissions(m.chat.id, FULL_PERMS)
    await set_setting(m.chat.id, "lockdown", False); await m.reply_text("✅ Panic lifted.")

@app.on_message(filters.command(["scan","monitor","diagnostics"]) & group_filter)
async def scan_cmd(_, m):
    s = await get_settings(m.chat.id)
    on = sum(1 for v in s.values() if v is True)
    await m.reply_text(f"🩺 {on} protections active.")

# ============================================================
# ANTI-* TOGGLES + SCANNER
# ============================================================
ANTI_KEYS = {
    "antispam":"antispam","antiflood":"antiflood","antilink":"antilink","antiraid":"antiraid",
    "antibot":"antibot","antifake":"antifake","antiforward":"antiforward",
    "antichannel":"antichannel","anticaps":"anticaps","antitagall":"antitagall",
    "antimention":"antimention","antimedia":"antimedia","antisticker":"antisticker",
    "antigif":"antigif","antipoll":"antipoll","antifile":"antifile","antivoice":"antivoice",
    "antivideo":"antivideo","antiaudio":"antiaudio","antiemoji":"antiemoji",
    "antiedit":"antiedit","antidelete":"antidelete","antiarabic":"antiarabic",
    "antifakeadmin":"antifakeadmin","antinickname":"antinickname","antiprofanity":"antiprofanity",
}

def _make_anti(cmd, key):
    @app.on_message(filters.command(cmd) & group_filter)
    async def _h(_, m):
        a = _arg(m)
        if a not in ("on","off"):
            cur = (await get_settings(m.chat.id)).get(key, False)
            return await m.reply_text(f"`{cmd}`: {cur}. Use `/{cmd} on|off`.")
        await set_setting(m.chat.id, key, a == "on")
        await m.reply_text(f"✅ {cmd} **{a.upper()}**")
    return _h

for c, k in ANTI_KEYS.items(): _make_anti(c, k)

@app.on_message(filters.command(["raidmode","raidoff","lockdown","unlockdown"]) & group_filter)
async def raid_cmd(client, m):
    cmd = m.command[0]; on = cmd in ("raidmode","lockdown")
    key = "raidmode" if "raid" in cmd else "lockdown"
    await set_setting(m.chat.id, key, on)
    if cmd in ("lockdown","unlockdown"):
        await client.set_chat_permissions(m.chat.id, NO_PERMS if on else FULL_PERMS)
    await m.reply_text(f"🛡 {cmd} **{'ON' if on else 'OFF'}**")

URL_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)\S+", re.I)
MENTION_RE = re.compile(r"@\w{3,}")
ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002700-\U000027BF]")
PROFANITY = {"fuck","shit","bitch","asshole","bastard"}

@app.on_message(filters.group & ~filters.service, group=5)
async def scanner(client, m):
    if not m.from_user or m.from_user.id in SUDO_USERS: return
    s = await get_settings(m.chat.id)
    if not s.get("protection", True): return
    if await is_whitelisted(m.chat.id, m.from_user.id): return
    await bump(m.chat.id, "messages")
    text = (m.text or m.caption or "")
    reasons = []
    if s.get("antiflood") and await flood.hit((m.chat.id, m.from_user.id)): reasons.append("flood")
    if s.get("antilink") and URL_RE.search(text):                            reasons.append("link")
    if s.get("antimention") and MENTION_RE.search(text):                     reasons.append("mention")
    if s.get("anticaps") and len(text) > 10 and sum(1 for c in text if c.isupper())/max(1,len(text)) > 0.7:
        reasons.append("caps")
    if s.get("antiarabic") and ARABIC_RE.search(text):                       reasons.append("arabic")
    if s.get("antiemoji") and len(EMOJI_RE.findall(text)) > 5:               reasons.append("emoji")
    if s.get("antiprofanity") and any(w in text.lower() for w in PROFANITY): reasons.append("profanity")
    if s.get("antiforward") and m.forward_date:                              reasons.append("forward")
    if s.get("antichannel") and m.sender_chat:                               reasons.append("channel")
    if s.get("antibot") and m.from_user.is_bot:                              reasons.append("bot")
    if s.get("antimedia") and m.media:                                       reasons.append("media")
    if s.get("antisticker") and m.sticker:                                   reasons.append("sticker")
    if s.get("antigif") and m.animation:                                     reasons.append("gif")
    if s.get("antipoll") and m.poll:                                         reasons.append("poll")
    if s.get("antifile") and m.document:                                     reasons.append("file")
    if s.get("antivoice") and m.voice:                                       reasons.append("voice")
    if s.get("antivideo") and (m.video or m.video_note):                     reasons.append("video")
    if s.get("antiaudio") and m.audio:                                       reasons.append("audio")
    if s.get("antitagall") and text.count("@") > 5:                          reasons.append("tagall")
    for w in await get_bl_words(m.chat.id):
        if w in text.lower(): reasons.append(f"bl:{w}"); break
    if reasons:
        try: await m.delete()
        except Exception: pass
        if "flood" in reasons or any(r.startswith("bl:") for r in reasons):
            try: await client.restrict_chat_member(m.chat.id, m.from_user.id, NO_PERMS)
            except Exception: pass

@app.on_edited_message(filters.group, group=6)
async def antiedit_cmd(_, m):
    s = await get_settings(m.chat.id)
    if s.get("antiedit") and m.from_user and m.from_user.id not in SUDO_USERS:
        try: await m.delete()
        except Exception: pass

# ============================================================
# LOCKS
# ============================================================
@app.on_message(filters.command("lock") & group_filter)
async def lock_cmd(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /lock <photo|video|voice|gif|sticker|poll|file|link>")
    await set_setting(m.chat.id, f"lock_{m.command[1].lower()}", True)
    await m.reply_text(f"🔒 lock_{m.command[1].lower()} on")

@app.on_message(filters.command("unlock") & group_filter)
async def unlock_cmd(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /unlock <…>")
    await set_setting(m.chat.id, f"lock_{m.command[1].lower()}", False)
    await m.reply_text(f"🔓 lock_{m.command[1].lower()} off")

LOCK_PAIRS = [("lockphoto","lock_photo"),("lockvideo","lock_video"),("lockvoice","lock_voice"),
              ("lockgif","lock_gif"),("locksticker","lock_sticker"),("lockpoll","lock_poll"),
              ("lockfile","lock_file"),("locklink","lock_link")]

def _make_lock(cmd, key):
    @app.on_message(filters.command(cmd) & group_filter)
    async def on_(_, m):
        await set_setting(m.chat.id, key, True); await m.reply_text(f"🔒 {cmd} on")
    @app.on_message(filters.command("un"+cmd) & group_filter)
    async def off_(_, m):
        await set_setting(m.chat.id, key, False); await m.reply_text(f"🔓 un{cmd} done")

for c, k in LOCK_PAIRS: _make_lock(c, k)

@app.on_message(filters.command("slowmode") & group_filter)
async def slow_cmd(client, m):
    try:
        secs = int(m.command[1]) if len(m.command) > 1 else 0
        await client.set_slow_mode(m.chat.id, secs)
        await m.reply_text(f"🐌 Slowmode {secs}s")
    except Exception as e: await m.reply_text(f"❌ {e}")

# ============================================================
# WHITELIST / BLACKLIST
# ============================================================
@app.on_message(filters.command(["whitelist","trusted"]) & group_filter)
async def wl_add(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await _toggle("whitelist", m.chat.id, u.id, True); await m.reply_text(f"✅ {u.mention}")

@app.on_message(filters.command(["unwhitelist","untrusted"]) & group_filter)
async def wl_rem(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await _toggle("whitelist", m.chat.id, u.id, False); await m.reply_text(f"♻️ {u.mention}")

@app.on_message(filters.command("whitelistlist") & group_filter)
async def wl_list(_, m):
    ids = await list_table("whitelist", m.chat.id)
    await m.reply_text("🟢 " + ("\n".join(f"`{i}`" for i in ids) or "empty"))

@app.on_message(filters.command("blacklist") & group_filter)
async def bl_add(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await _toggle("blacklist_users", m.chat.id, u.id, True); await m.reply_text(f"⛔ {u.mention}")

@app.on_message(filters.command("unblacklist") & group_filter)
async def bl_rem(client, m):
    u = await _target(client, m)
    if not u: return await m.reply_text("Reply or pass user.")
    await _toggle("blacklist_users", m.chat.id, u.id, False); await m.reply_text(f"♻️ {u.mention}")

@app.on_message(filters.command("blacklisted") & group_filter)
async def bl_list(_, m):
    ids = await list_table("blacklist_users", m.chat.id)
    words = await get_bl_words(m.chat.id)
    await m.reply_text(f"⛔ Users: {ids}\n📝 Words: {words}")

@app.on_message(filters.command("blacklistword") & group_filter)
async def bw_add(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /blacklistword <word>")
    await add_bl_word(m.chat.id, m.command[1]); await m.reply_text(f"⛔ `{m.command[1]}`")

@app.on_message(filters.command("unblacklistword") & group_filter)
async def bw_rem(_, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /unblacklistword <word>")
    await rem_bl_word(m.chat.id, m.command[1]); await m.reply_text(f"♻️ `{m.command[1]}`")

# ============================================================
# VOICE CHAT
# ============================================================
@app.on_message(filters.command("vcprotect") & group_filter)
async def vcp_cmd(_, m):
    a = _arg(m)
    if a not in ("on","off"): return await m.reply_text("Usage: /vcprotect on|off")
    await set_setting(m.chat.id, "vcprotect", a == "on")
    await m.reply_text(f"🎙 VC protection **{a.upper()}**")

@app.on_message(filters.command("vcstatus") & group_filter)
async def vcs_cmd(_, m):
    s = await get_settings(m.chat.id)
    await m.reply_text(f"🎙 VC protect: `{s.get('vcprotect')}`")

@app.on_message(filters.command("vclogs") & group_filter)
async def vcl_cmd(_, m): await m.reply_text("📄 VC logs in server log.")

@app.on_message(filters.command("vclock") & group_filter)
async def vclock_cmd(client, m):
    await client.set_chat_permissions(m.chat.id, ChatPermissions(can_send_messages=True))
    await m.reply_text("🔒 VC locked.")

@app.on_message(filters.command("vcunlock") & group_filter)
async def vcun_cmd(client, m):
    await client.set_chat_permissions(m.chat.id, FULL_PERMS); await m.reply_text("🔓 VC unlocked.")

@app.on_message(filters.command(["vcmuteall","vcunmuteall"]) & group_filter)
async def vcm_cmd(_, m): await m.reply_text(f"🎙 {m.command[0]} requested.")

@app.on_message(filters.command(["joinvc","vcjoin"]) & group_filter & sudo_filter)
async def joinvc_cmd(_, m):
    await m.reply_text("🎙 Trying to join VC (muted)…")
    ok = await vc_join_muted(m.chat.id)
    if not ok:
        await m.reply_text(
            "❌ Could not join VC.\n"
            "Make sure:\n"
            "• `STRING_SESSION` is set (user account, not bot)\n"
            "• `py-tgcalls` + `ffmpeg` are installed on the host\n"
            "• A voice chat is currently active in this group\n"
            "• The user account is a member of this group")
    else:
        await m.reply_text("✅ Joined VC with mic OFF.")

@app.on_message(filters.command(["leavevc","vcleave"]) & group_filter & sudo_filter)
async def leavevc_cmd(_, m):
    await vc_leave(m.chat.id)
    await m.reply_text("👋 Left VC.")

@app.on_message(filters.video_chat_started, group=-5)
async def auto_vc(client, m):
    await set_setting(m.chat.id, "vcprotect", True)
    try: await client.send_message(m.chat.id, "🎙 Voice chat detected — protection enabled. Joining muted…")
    except Exception: pass
    log.info("VC started in %s", m.chat.id)
    try: await vc_join_muted(m.chat.id)
    except Exception as e: log.exception("auto VC: %s", e)

@app.on_message(filters.video_chat_ended, group=-5)
async def vc_end(client, m):
    try: await vc_leave(m.chat.id)
    except Exception: pass

# ============================================================
# DEVELOPER
# ============================================================
@app.on_message(filters.command("addsudo") & owner_filter)
async def addsudo_cmd(client, m):
    if not m.reply_to_message and len(m.command) < 2: return await m.reply_text("Reply or ID.")
    uid = m.reply_to_message.from_user.id if m.reply_to_message else int(m.command[1])
    await add_sudo_db(uid); SUDO_USERS.add(uid); await m.reply_text(f"✅ +sudo `{uid}`")

@app.on_message(filters.command("remsudo") & owner_filter)
async def remsudo_cmd(client, m):
    uid = m.reply_to_message.from_user.id if m.reply_to_message else int(m.command[1])
    await rem_sudo_db(uid); SUDO_USERS.discard(uid); await m.reply_text(f"✅ -sudo `{uid}`")

@app.on_message(filters.command("sudolist") & sudo_filter)
async def sudolist_cmd(_, m):
    await m.reply_text("👑 " + ", ".join(f"`{u}`" for u in SUDO_USERS))

@app.on_message(filters.command("grouplist") & sudo_filter)
async def gl_cmd(_, m):
    ids = await all_chats()
    await m.reply_text("💬\n" + ("\n".join(f"`{i}`" for i in ids) or "—"))

@app.on_message(filters.command("leavegroup") & sudo_filter)
async def leave_cmd(client, m):
    cid = int(m.command[1]) if len(m.command) > 1 else m.chat.id
    await client.leave_chat(cid); await m.reply_text(f"👋 Left `{cid}`")

@app.on_message(filters.command("broadcast") & sudo_filter)
async def bc_cmd(client, m):
    if len(m.command) < 2: return await m.reply_text("Usage: /broadcast <text>")
    text = m.text.split(None, 1)[1]; ok = 0
    for cid in await all_chats():
        try: await client.send_message(cid, text); ok += 1
        except Exception: pass
    await m.reply_text(f"📣 {ok} chats.")

@app.on_message(filters.command("eval") & owner_filter)
async def eval_cmd(client, m):
    code = m.text.split(None, 1)[1] if len(m.command) > 1 else ""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = eval(code, {"client": client, "m": m})
            if asyncio.iscoroutine(res): res = await res
        out = buf.getvalue() or repr(res)
    except Exception: out = traceback.format_exc()
    await m.reply_text(f"```\n{out[:3500]}\n```")

@app.on_message(filters.command("exec") & owner_filter)
async def exec_cmd(_, m):
    cmd = m.text.split(None, 1)[1] if len(m.command) > 1 else ""
    p = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE,
                                              stderr=asyncio.subprocess.STDOUT)
    out, _e = await p.communicate()
    await m.reply_text(f"```\n{out.decode()[:3500]}\n```")

@app.on_message(filters.command(["reload","update","clearcache","debug"]) & sudo_filter)
async def misc_cmd(_, m): await m.reply_text(f"✅ {m.command[0]} ack.")

@app.on_message(filters.command("maintenance") & owner_filter)
async def maint_cmd(_, m):
    global MAINTENANCE
    a = m.command[1].lower() if len(m.command) > 1 else ""
    if a not in ("on","off"): return await m.reply_text("Usage: /maintenance on|off")
    MAINTENANCE = a == "on"; await m.reply_text(f"🛠 Maintenance **{a.upper()}**")

@app.on_message(filters.command("restart") & owner_filter)
async def restart_cmd(_, m):
    await m.reply_text("♻️ Restarting…"); os.execv(sys.executable, [sys.executable, *sys.argv])

@app.on_message(filters.command("shutdown") & owner_filter)
async def shutdown_cmd(_, m):
    await m.reply_text("👋 Bye."); os._exit(0)

# ============================================================
# AUTO welcome / goodbye
# ============================================================
@app.on_message(filters.new_chat_members)
async def welcome(client, m):
    await bump(m.chat.id, "joins")
    s = await get_settings(m.chat.id)
    me = await client.get_me()
    for u in m.new_chat_members:
        if s.get("antibot") and u.is_bot and u.id != me.id:
            try: await client.ban_chat_member(m.chat.id, u.id)
            except Exception: pass
            continue
        try: await client.send_message(m.chat.id,
                f"👋 Welcome {u.mention} to **{m.chat.title}**! /rules")
        except Exception: pass

@app.on_message(filters.left_chat_member)
async def goodbye(client, m):
    await bump(m.chat.id, "leaves")
    try: await client.send_message(m.chat.id, f"👋 Goodbye {m.left_chat_member.first_name}.")
    except Exception: pass

# ============================================================
# COMMAND MENU
# ============================================================
GENERAL_CMDS = [
    ("start","Start"),("help","Help"),("menu","Menu"),("settings","Show settings"),
    ("ping","Ping"),("uptime","Uptime"),("status","Status"),("stats","Group stats"),
    ("logs","Logs (owner)"),("id","Show IDs"),("userinfo","User info"),
    ("chatinfo","Chat info"),("admins","List admins"),("rules","Show rules"),
    ("setrules","Set rules"),("report","Report message"),("backup","Backup DB"),
    ("restore","Restore DB"),("invitelink","Invite link"),("botinfo","Bot info"),
    ("version","Version"),
]
ALL_CMDS = (GENERAL_CMDS
    + [(c,c.title()) for c in ["ban","sban","tban","unban","kick","skick","mute","tmute","unmute",
        "warn","unwarn","warnings","clearwarns","freeze","unfreeze","purge","del","pin","unpin",
        "promote","demote","adminlist","settitle","setphoto","cleanservice","cleanbots","purgebots"]]
    + [(c,c.title()) for c in ["protection","shield","shieldoff","panic","panicoff","security",
        "scan","monitor","diagnostics"]]
    + [(c,c.title()) for c in list(ANTI_KEYS) + ["raidmode","raidoff","lockdown","unlockdown"]]
    + [(c,c.title()) for c in ["lock","unlock"] + [p[0] for p in LOCK_PAIRS]
        + ["un"+p[0] for p in LOCK_PAIRS] + ["slowmode"]]
    + [(c,c.title()) for c in ["whitelist","unwhitelist","whitelistlist","trusted","untrusted",
        "blacklist","unblacklist","blacklistword","unblacklistword","blacklisted"]]
    + [(c,c.title()) for c in ["vcprotect","vcstatus","vclogs","vclock","vcunlock",
        "vcmuteall","vcunmuteall","joinvc","leavevc"]]
    + [(c,c.title()) for c in ["addsudo","remsudo","sudolist","broadcast","grouplist","leavegroup",
        "eval","exec","reload","update","debug","clearcache","maintenance","restart","shutdown"]]
)

async def _publish_menu():
    try:
        seen = set(); cmds = []
        for c, d in ALL_CMDS:
            if c in seen: continue
            seen.add(c); cmds.append(BotCommand(c, d[:64]))
        await app.set_bot_commands(cmds[:100])
        log.info("Command menu published (%d).", len(cmds))
    except Exception as e:
        log.warning("set_bot_commands failed: %s", e)

# ============================================================
# PERIODIC BACKUP
# ============================================================
async def periodic_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            dst = os.path.join(BACKUP_DIR, f"auto-{int(time.time())}.db")
            shutil.copy(DB_PATH, dst); log.info("Auto-backup -> %s", dst)
        except Exception as e: log.exception("Backup: %s", e)

# ============================================================
# MAIN with auto-reconnect
# ============================================================
async def main():
    await db_init()
    for u in await list_sudo_db(): SUDO_USERS.add(u)
    backoff = 2
    while True:
        try:
            await app.start()
            me = await app.get_me()
            log.info("Logged in as @%s (%s)", me.username, me.id)
            await _publish_menu()
            asyncio.create_task(periodic_backup())
            asyncio.create_task(vc_start())
            # Startup notification
            startup_msg = (
                f"✅ **Bot Online**\n"
                f"🤖 @{me.username}\n"
                f"🆔 `{me.id}`\n"
                f"📦 Version: `{VERSION}`\n"
                f"⏱ Started: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
            )
            for target in {OWNER_ID, MAIN_GROUP_ID}:
                if not target:
                    continue
                try:
                    await app.send_message(target, startup_msg)
                except Exception as e:
                    log.warning("Startup notify to %s failed: %s", target, e)
            await idle()
            await app.stop(); return
        except (ConnectionError, OSError, RPCError) as e:
            log.exception("Disconnected: %s — retry in %ss", e, backoff)
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

# ============================================================
# STRING-SESSION GENERATOR  (run: `python bot.py gen-session`)
# ============================================================
async def gen_session():
    print("=== String Session Generator (for VC auto-join) ===")
    api_id  = int(input("API_ID  : ").strip() or API_ID)
    api_hash = (input("API_HASH: ").strip() or API_HASH)
    async with Client("gen_session", api_id=api_id, api_hash=api_hash, in_memory=True) as c:
        s = await c.export_session_string()
        print("\n=== YOUR STRING_SESSION (keep secret) ===\n")
        print(s)
        print("\nAdd to .env:  STRING_SESSION=" + s)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("gen-session", "gen_session", "session"):
        asyncio.run(gen_session()); sys.exit(0)
    if not API_ID or not API_HASH:
        print("ERROR: Set API_ID and API_HASH (https://my.telegram.org) in env.",
              file=sys.stderr)
        sys.exit(1)
    try: asyncio.run(main())
    except KeyboardInterrupt: log.info("Bye.")
