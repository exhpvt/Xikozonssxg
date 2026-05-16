#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║        EXHAUST HOSTING — Ultimate Bot Platform           ║
║         24/7 Python Bot Hosting | Premium UI            ║
╚══════════════════════════════════════════════════════════╝
"""

import os, sys, asyncio, json, zipfile, shutil, subprocess
import time, signal, threading
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import psutil

# ══════════════════════════════════════════════════════════════
#  CONFIG  —  edit these or set as env vars
# ══════════════════════════════════════════════════════════════

BOT_TOKEN    = os.environ.get("8738189905:AAH5i2SuVqQMHdkTSdkckm3lCVrsQXBRORY", "8738189905:AAH5i2SuVqQMHdkTSdkckm3lCVrsQXBRORY")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "7082733957"))
LOG_CHANNEL  = int(os.environ.get("LOG_CHANNEL", "-1003608585339"))
BOT_USERNAME = ""   # filled at runtime via get_me()

BASE_DIR    = Path(__file__).parent
BOTS_DIR    = BASE_DIR / "bots"
UPLOADS_DIR = BASE_DIR / "uploads"
STATE_FILE  = BASE_DIR / "bots_state.json"
USERS_FILE  = BASE_DIR / "users_state.json"

BOTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════════════

running_bots: dict        = {}
crash_notifications: list = []

users_db: dict = {
    "all_users":       [],
    "subscribed":      [],
    "bot_locked":      False,
    "owner_username":  "",
    "updates_channel": "",
}

# ══════════════════════════════════════════════════════════════
#  PERSISTENCE
# ══════════════════════════════════════════════════════════════

def load_state():
    global running_bots
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text())
        for bot_id, info in data.items():
            running_bots[bot_id] = {
                "name":         info.get("name", bot_id),
                "type":         info.get("type", "unknown"),
                "path":         info.get("path", ""),
                "pid":          None,
                "process":      None,
                "start_time":   None,
                "upload_time":  info.get("upload_time"),
                "auto_restart": info.get("auto_restart", True),
                "uploaded_by":  info.get("uploaded_by", 0),
                "was_running":  info.get("was_running", False),
            }
    except Exception as e:
        print(f"[STATE] Load error: {e}", flush=True)
        running_bots = {}


def load_users():
    global users_db
    if USERS_FILE.exists():
        try:
            users_db = json.loads(USERS_FILE.read_text())
        except Exception:
            pass


def save_state():
    data = {}
    for bid, i in running_bots.items():
        data[bid] = {
            "name":         i["name"],
            "type":         i["type"],
            "path":         i["path"],
            "start_time":   i.get("start_time"),
            "upload_time":  i.get("upload_time"),
            "auto_restart": i.get("auto_restart", True),
            "uploaded_by":  i.get("uploaded_by", 0),
            "was_running":  get_status(bid) == "running",
        }
    STATE_FILE.write_text(json.dumps(data, indent=2))


def save_users():
    USERS_FILE.write_text(json.dumps(users_db, indent=2))


def register_user(uid: int):
    if str(uid) not in users_db["all_users"]:
        users_db["all_users"].append(str(uid))
        save_users()

# ══════════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════════

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


def is_subscribed(uid: int) -> bool:
    if is_admin(uid):
        return True
    if not users_db.get("bot_locked", False):
        return True
    return str(uid) in users_db.get("subscribed", [])


def owns_bot(uid: int, bot_id: str) -> bool:
    """✅ SECURITY FIX: Only admin OR the original uploader can control a bot."""
    if is_admin(uid):
        return True
    if bot_id in running_bots:
        return int(running_bots[bot_id].get("uploaded_by", -1)) == uid
    return False

# ══════════════════════════════════════════════════════════════
#  PROCESS MANAGEMENT
# ══════════════════════════════════════════════════════════════

def get_status(bot_id: str) -> str:
    if bot_id not in running_bots:
        return "unknown"
    proc = running_bots[bot_id].get("process")
    if proc is not None:
        if proc.poll() is None:
            return "running"
        running_bots[bot_id]["process"] = None
        running_bots[bot_id]["pid"]     = None
    return "stopped"


def start_bot(bot_id: str) -> tuple[bool, str]:
    if bot_id not in running_bots:
        return False, "Bot nahi mila."
    info     = running_bots[bot_id]
    bot_path = Path(info["path"])
    if not bot_path.exists():
        return False, "Bot file delete ho gayi."
    if get_status(bot_id) == "running":
        return False, "Bot pehle se chal raha hai."

    if info["type"] == "single":
        cmd = [sys.executable, str(bot_path)]
        cwd = str(bot_path.parent)
    elif info["type"] == "zip":
        main = bot_path / "main.py"
        if not main.exists():
            py_files = list(bot_path.glob("*.py"))
            if not py_files:
                return False, "main.py nahi mili ZIP mein."
            main = py_files[0]
        cmd = [sys.executable, str(main)]
        cwd = str(bot_path)
    else:
        return False, "Unknown bot type."

    try:
        log_f = open(BOTS_DIR / f"{bot_id}.log", "a")
        log_f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] === BOT STARTED ===\n")
        log_f.flush()
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=os.environ.copy(),
            stdout=log_f, stderr=log_f,
            preexec_fn=os.setsid,
        )
        running_bots[bot_id].update(
            process=proc, pid=proc.pid, start_time=time.time()
        )
        save_state()
        return True, f"Bot start ho gaya! PID: {proc.pid}"
    except Exception as e:
        return False, f"Error: {e}"


def stop_bot(bot_id: str) -> tuple[bool, str]:
    if bot_id not in running_bots:
        return False, "Bot nahi mila."
    proc = running_bots[bot_id].get("process")
    if proc is None or proc.poll() is not None:
        running_bots[bot_id].update(process=None, pid=None)
        return False, "Bot pehle se band hai."
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        running_bots[bot_id].update(process=None, pid=None)
        save_state()
        return True, "Bot band ho gaya."
    except Exception as e:
        return False, f"Error: {e}"


def get_last_error(bot_id: str, lines: int = 30) -> str:
    log_f = BOTS_DIR / f"{bot_id}.log"
    if not log_f.exists():
        return "(no logs found)"
    try:
        content = log_f.read_text(errors="replace").splitlines()
        last    = content[-lines:] if len(content) > lines else content
        return "\n".join(last)
    except Exception as e:
        return f"(log read error: {e})"

# ══════════════════════════════════════════════════════════════
#  WATCHDOG
# ══════════════════════════════════════════════════════════════

def watchdog_loop():
    while True:
        try:
            for bot_id, info in list(running_bots.items()):
                if not info.get("auto_restart", True):
                    continue
                proc = info.get("process")
                if proc is not None and proc.poll() is not None:
                    exit_code     = proc.returncode
                    error_snippet = get_last_error(bot_id, 25)
                    user_id       = info.get("uploaded_by", 0)
                    running_bots[bot_id].update(process=None, pid=None)
                    if user_id:
                        crash_notifications.append({
                            "user_id":   int(user_id),
                            "bot_name":  info["name"],
                            "bot_id":    bot_id,
                            "exit_code": exit_code,
                            "error":     error_snippet,
                        })
                    ok, msg = start_bot(bot_id)
                    print(f"[WATCHDOG] {bot_id} → {msg}", flush=True)
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}", flush=True)
        time.sleep(10)


def auto_start_bots():
    started = 0
    for bot_id, info in running_bots.items():
        if info.get("was_running", False) or info.get("auto_restart", True):
            ok, msg = start_bot(bot_id)
            if ok:
                started += 1
            print(f"[AUTOSTART] {bot_id}: {msg}", flush=True)
    return started


async def send_crash_notifications(context: ContextTypes.DEFAULT_TYPE):
    while crash_notifications:
        notif = crash_notifications.pop(0)
        uid   = notif.get("user_id")
        if not uid:
            continue
        error = notif.get("error", "").strip()
        if len(error) > 800:
            error = "..." + error[-800:]
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"⚠️ Bot Crashed & Restarted!\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📛 Bot: {notif['bot_name']}\n"
                    f"🆔 ID: {notif['bot_id']}\n"
                    f"💥 Exit Code: {notif.get('exit_code', '?')}\n"
                    f"🔄 Status: Auto-restarted ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📋 Last Error:\n"
                    f"{error or '(no output)'}"
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 View Logs",  callback_data=f"logs_{notif['bot_id']}"),
                    InlineKeyboardButton("🔄 Restart",    callback_data=f"restart_{notif['bot_id']}"),
                ]]),
            )
        except Exception as e:
            print(f"[NOTIFY] Failed: {e}", flush=True)

# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def fmt_uptime(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def cb(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def url_btn(label: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, url=url)


def back_btn() -> InlineKeyboardButton:
    return cb("🔙 Back", "menu")

# ══════════════════════════════════════════════════════════════
#  BOTTOM REPLY KEYBOARD
#  These appear as big colorful buttons at the bottom of chat.
#  Telegram renders them with the emoji color naturally.
# ══════════════════════════════════════════════════════════════

def main_reply_keyboard(uid: int) -> ReplyKeyboardMarkup:
    if is_admin(uid):
        rows = [
            [KeyboardButton("🚀 Upload Bot"),    KeyboardButton("💎 My Bots")],
            [KeyboardButton("⚡ Speed"),          KeyboardButton("📊 Stats"),        KeyboardButton("🔢 Running")],
            [KeyboardButton("👑 Admin Panel"),    KeyboardButton("🎙 Broadcast"),     KeyboardButton("🔒 Lock Bot")],
            [KeyboardButton("🎊 Subscriptions"),  KeyboardButton("👻 Contact Owner")],
        ]
    else:
        rows = [
            [KeyboardButton("🚀 Upload Bot"),   KeyboardButton("💎 My Bots")],
            [KeyboardButton("⚡ Speed"),         KeyboardButton("📊 Stats"),       KeyboardButton("🔢 Running")],
            [KeyboardButton("🎊 Subscriptions"), KeyboardButton("👻 Contact Owner")],
        ]
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        input_field_placeholder="⚡ EXHAUST HOSTING — Choose option...",
    )

# ══════════════════════════════════════════════════════════════
#  MENU TEXT & INLINE MARKUP BUILDERS
# ══════════════════════════════════════════════════════════════

def welcome_text(first_name: str) -> str:
    return (
        f"✨ Hello {first_name}!\n\n"
        f"🔥 EXHAUST HOSTING\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🐍 Single .py file bots\n"
        f"📦 Multi-file .zip bots\n"
        f"⚡ 24/7 Auto-restart on crash\n"
        f"📲 Crash errors auto-notify\n"
        f"🛡️ Private bot controls\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 Neeche se option chuno:"
    )


def main_inline_menu(uid: int) -> InlineKeyboardMarkup:
    ch = users_db.get("updates_channel", "")
    ch_btn = (
        url_btn("📣 Updates Channel ↗", f"https://t.me/{ch.lstrip('@')}")
        if ch else cb("📣 Updates Channel", "updates_channel")
    )
    rows = [
        [ch_btn],
        [cb("🚀 Upload Bot",      "upload_info"),   cb("💎 My Bots",        "check_files")],
        [cb("⚡ Speed Test",      "bot_speed"),     cb("📊 Statistics",     "stats")],
        [cb("🎊 Subscriptions",   "subscriptions"), cb("🔢 Running Code",   "running_all")],
        [cb("👻 Contact Owner",   "contact_owner")],
    ]
    if is_admin(uid):
        rows.insert(-1, [
            cb("👑 Admin Panel", "admin_panel"),
            cb("🎙 Broadcast",   "broadcast_info"),
            cb("🔒 Lock Bot",    "lock_bot"),
        ])
    return InlineKeyboardMarkup(rows)


def bots_list_view(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Shows ONLY bots that belong to this user (or all bots if admin)."""
    user_bots = {
        bid: info for bid, info in running_bots.items()
        if is_admin(uid) or int(info.get("uploaded_by", -1)) == uid
    }

    if not user_bots:
        return (
            "📂 No Bots Found\n\nPehle .py ya .zip upload karo!",
            InlineKeyboardMarkup([
                [cb("🚀 Upload Karo", "upload_info")],
                [back_btn()],
            ]),
        )

    text = "💎 Tumhare Hosted Bots\n━━━━━━━━━━━━━━━━━\n\n"
    kb   = []

    for bid, info in user_bots.items():
        st   = get_status(bid)
        em   = "🟢" if st == "running" else "🔴"
        secs = int(time.time() - info["start_time"]) if st == "running" and info.get("start_time") else 0
        up   = f" ⏱ {fmt_uptime(secs)}" if secs > 0 else ""
        text += f"{em} {info['name']}{up}\n   ID: {bid} • {info['type'].upper()}\n\n"

        # Only owner/admin sees control buttons for each bot
        row = []
        if st == "running":
            row.append(cb("⏹️ Stop",   f"stop_{bid}"))
        else:
            row.append(cb("▶️ Start",  f"start_{bid}"))
        row.append(cb("📋 Logs",       f"logs_{bid}"))
        row.append(cb("🗑️ Delete",    f"delete_{bid}"))
        kb.append(row)

    kb.append([back_btn()])
    return text, InlineKeyboardMarkup(kb)

# ══════════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)

    if not is_subscribed(user.id):
        owner   = users_db.get("owner_username", "")
        contact = f"\n\n💬 Contact: @{owner}" if owner else ""
        await update.message.reply_text(
            f"🔒 Bot Locked!\n\nSirf subscribed users use kar sakte hain.{contact}"
        )
        return

    # Send welcome with inline buttons
    await update.message.reply_text(
        welcome_text(user.first_name),
        reply_markup=main_inline_menu(user.id),
    )
    # Send persistent bottom keyboard as a separate message
    await update.message.reply_text(
        "👇 Quick access:",
        reply_markup=main_reply_keyboard(user.id),
    )

# ══════════════════════════════════════════════════════════════
#  REPLY KEYBOARD TEXT HANDLER
# ══════════════════════════════════════════════════════════════

KEYBOARD_MAP = {
    "🚀 Upload Bot":     "upload_info",
    "💎 My Bots":        "check_files",
    "⚡ Speed":          "bot_speed",
    "📊 Stats":          "stats",
    "🔢 Running":        "running_all",
    "👑 Admin Panel":    "admin_panel",
    "🎙 Broadcast":      "broadcast_info",
    "🔒 Lock Bot":       "lock_bot",
    "🎊 Subscriptions":  "subscriptions",
    "👻 Contact Owner":  "contact_owner",
}


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)
    text = update.message.text

    if text not in KEYBOARD_MAP:
        return  # not a keyboard button, ignore

    if not is_subscribed(user.id):
        await update.message.reply_text("🔒 Access denied.")
        return

    await _run_action(update, ctx, user.id, KEYBOARD_MAP[text], via_message=True)

# ══════════════════════════════════════════════════════════════
#  CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════

async def btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    user = q.from_user
    register_user(user.id)
    await q.answer()
    data = q.data

    # ── Named menu actions ──
    named = {
        "menu", "upload_info", "check_files", "bot_speed", "stats",
        "running_all", "admin_panel", "broadcast_info", "lock_bot",
        "subscriptions", "contact_owner", "updates_channel",
    }
    if data in named:
        await _run_action(update, ctx, user.id, data, via_message=False)
        return

    # ── Start bot ── SECURITY: owner only
    if data.startswith("start_"):
        bid = data[6:]
        if not owns_bot(user.id, bid):
            await q.answer("🚫 Sirf apna bot control kar sakte ho!", show_alert=True)
            return
        running_bots[bid]["auto_restart"] = True
        ok, msg = start_bot(bid)
        await q.answer(msg, show_alert=True)
        await _refresh_bots(q, user.id)
        return

    # ── Stop bot ── SECURITY: owner only
    if data.startswith("stop_"):
        bid = data[5:]
        if not owns_bot(user.id, bid):
            await q.answer("🚫 Sirf apna bot control kar sakte ho!", show_alert=True)
            return
        running_bots[bid]["auto_restart"] = False
        ok, msg = stop_bot(bid)
        await q.answer(msg, show_alert=True)
        await _refresh_bots(q, user.id)
        return

    # ── Restart (from crash notification) ── SECURITY: owner only
    if data.startswith("restart_"):
        bid = data[8:]
        if not owns_bot(user.id, bid):
            await q.answer("🚫 Sirf apna bot control kar sakte ho!", show_alert=True)
            return
        stop_bot(bid)
        running_bots[bid]["auto_restart"] = True
        ok, msg = start_bot(bid)
        await q.answer(msg, show_alert=True)
        return

    # ── Logs ── SECURITY: owner only
    if data.startswith("logs_"):
        bid = data[5:]
        if not owns_bot(user.id, bid):
            await q.answer("🚫 Sirf apna bot ke logs dekh sakte ho!", show_alert=True)
            return
        log_f = BOTS_DIR / f"{bid}.log"
        kb    = InlineKeyboardMarkup([[cb("🔙 Back to Bots", "check_files")]])
        if not log_f.exists():
            try:
                await q.edit_message_text("❌ No logs found.", reply_markup=kb)
            except Exception:
                pass
            return
        lines   = log_f.read_text(errors="replace").splitlines()
        last    = "\n".join(lines[-25:]) if len(lines) > 25 else "\n".join(lines)
        snippet = (last.strip() or "(empty)")
        if len(snippet) > 3500:
            snippet = "..." + snippet[-3500:]
        try:
            await q.edit_message_text(
                f"📋 Logs — {bid}:\n\n{snippet}",
                reply_markup=kb,
            )
        except Exception:
            pass
        return

    # ── Delete confirmation ── SECURITY: owner only
    if data.startswith("delete_"):
        bid = data[7:]
        if not owns_bot(user.id, bid):
            await q.answer("🚫 Sirf apna bot delete kar sakte ho!", show_alert=True)
            return
        kb = InlineKeyboardMarkup([[
            cb("✅ Haan Delete Karo", f"confirm_del_{bid}"),
            cb("❌ Cancel",           "cancel_del"),
        ]])
        try:
            await q.edit_message_text(
                f"⚠️ Delete Bot?\n\n{bid} permanently delete karna chahte ho?\nYe undo nahi hoga!",
                reply_markup=kb,
            )
        except Exception:
            pass
        return

    if data == "cancel_del":
        await _refresh_bots(q, user.id)
        return

    if data.startswith("confirm_del_"):
        bid = data[12:]
        if not owns_bot(user.id, bid):
            await q.answer("🚫 Permission denied!", show_alert=True)
            return
        if bid in running_bots:
            stop_bot(bid)
            info = running_bots.pop(bid)
            p    = Path(info["path"])
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                p.unlink(missing_ok=True)
                if p.parent != BOTS_DIR:
                    shutil.rmtree(p.parent, ignore_errors=True)
            (BOTS_DIR / f"{bid}.log").unlink(missing_ok=True)
            save_state()
        await q.answer("🗑️ Bot delete ho gaya!", show_alert=True)
        await _refresh_bots(q, user.id)
        return

    # ── Toggle lock (admin only) ──
    if data == "toggle_lock":
        if not is_admin(user.id):
            await q.answer("❌ Admin only!", show_alert=True)
            return
        users_db["bot_locked"] = not users_db.get("bot_locked", False)
        save_users()
        locked = users_db["bot_locked"]
        await q.answer(f"{'🔒 Locked!' if locked else '🔓 Unlocked!'}", show_alert=True)
        try:
            await q.edit_message_text(
                f"🔒 Lock Bot\n━━━━━━━━━━━━━━━━━\n"
                f"Status: {'🔒 Locked' if locked else '🔓 Unlocked'}\n"
                f"Subscribed: {len(users_db.get('subscribed', []))}",
                reply_markup=InlineKeyboardMarkup([
                    [cb("🔓 Unlock" if locked else "🔒 Lock Now", "toggle_lock")],
                    [back_btn()],
                ]),
            )
        except Exception:
            pass
        return


async def _refresh_bots(q, uid: int):
    text, markup = bots_list_view(uid)
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
#  ACTION DISPATCHER  (shared by reply keyboard + callbacks)
# ══════════════════════════════════════════════════════════════

async def _run_action(
    update:      Update,
    ctx:         ContextTypes.DEFAULT_TYPE,
    uid:         int,
    action:      str,
    via_message: bool,
):
    q = None if via_message else update.callback_query

    async def send(text: str, markup=None):
        if via_message:
            await update.message.reply_text(text, reply_markup=markup)
        else:
            try:
                await q.edit_message_text(text, reply_markup=markup)
            except Exception:
                pass

    # ── Main menu ──
    if action == "menu":
        user = update.effective_user if via_message else q.from_user
        await send(welcome_text(user.first_name), main_inline_menu(uid))

    # ── Updates channel ──
    elif action == "updates_channel":
        ch = users_db.get("updates_channel", "")
        kb = []
        if ch:
            kb.append([url_btn(f"📣 Join {ch} ↗", f"https://t.me/{ch.lstrip('@')}")])
        kb.append([back_btn()])
        await send(
            "📣 Updates Channel\n━━━━━━━━━━━━━━━━━\n"
            + (f"Join karo: {ch}" if ch else "Abhi set nahi hai."),
            InlineKeyboardMarkup(kb),
        )

    # ── Upload info ──
    elif action == "upload_info":
        await send(
            "🚀 Upload Your Bot\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "🐍 Single Python File (.py):\n"
            "  • Direct .py file bhejo\n"
            "  • BOT_TOKEN file ke andar hona chahiye\n\n"
            "📦 ZIP Package (.zip):\n"
            "  • .zip mein sab files dalo\n"
            "  • Main entry: main.py (required)\n\n"
            "🌐 Language: Python 3.11 only\n"
            "🔄 Auto-restart on crash ✅\n"
            "⚠️ Errors auto-notify hoga ✅\n\n"
            "📲 Ab file bhejo ⬇️",
            InlineKeyboardMarkup([[back_btn()]]),
        )

    # ── Check files (my bots) ──
    elif action == "check_files":
        text, markup = bots_list_view(uid)
        await send(text, markup)

    # ── Speed test ──
    elif action == "bot_speed":
        t1   = time.time()
        await asyncio.sleep(0)
        lat  = (time.time() - t1) * 1000
        cpu  = psutil.cpu_percent(interval=0.3)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        await send(
            f"⚡ Speed Test\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🏓 Latency: {lat:.2f} ms\n"
            f"💻 CPU: {cpu:.1f}%\n"
            f"🧠 Free RAM: {mem.available/1024**2:.1f} MB\n"
            f"💾 Disk Free: {disk.free/1024**3:.1f} GB\n"
            f"🔄 Status: 24/7 Online ✅",
            InlineKeyboardMarkup([[back_btn()]]),
        )

    # ── Stats ──
    elif action == "stats":
        total = len(running_bots)
        run_c = sum(1 for bid in running_bots if get_status(bid) == "running")
        mem   = psutil.virtual_memory()
        cpu   = psutil.cpu_percent(interval=0.5)
        await send(
            f"📊 Statistics\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 Total Bots: {total} (🟢 Running: {run_c})\n"
            f"💻 CPU: {cpu:.1f}%\n"
            f"🧠 RAM: {mem.used/1024**2:.1f} / {mem.total/1024**2:.1f} MB\n\n"
            f"👥 Total Users: {len(users_db.get('all_users', []))}\n"
            f"✅ Subscribed: {len(users_db.get('subscribed', []))}\n"
            f"🔒 Locked: {'Yes' if users_db.get('bot_locked') else 'No'}\n"
            f"🌐 Language: Python 3.11\n"
            f"🔄 Watchdog: Active 24/7",
            InlineKeyboardMarkup([[back_btn()]]),
        )

    # ── Subscriptions ──
    elif action == "subscriptions":
        subs   = users_db.get("subscribed", [])
        locked = users_db.get("bot_locked", False)
        if is_admin(uid):
            text = (
                f"🎊 Subscriptions (Admin)\n━━━━━━━━━━━━━━━━━\n"
                f"🔒 Lock: {'ON' if locked else 'OFF'}\n"
                f"✅ Subscribed: {len(subs)}\n\n"
                f"Admin Commands:\n"
                f"/addsub <id>  •  /removesub <id>\n"
                f"/lock  •  /unlock"
            )
        else:
            st   = "✅ Subscribed" if str(uid) in subs else "❌ Not Subscribed"
            text = f"🎊 Subscriptions\n━━━━━━━━━━━━━━━━━\nYour status: {st}"
        await send(text, InlineKeyboardMarkup([[back_btn()]]))

    # ── Broadcast info ──
    elif action == "broadcast_info":
        if not is_admin(uid):
            await send("❌ Admin only.")
            return
        await send(
            f"🎙 Broadcast\n━━━━━━━━━━━━━━━━━\n"
            f"Send to all users:\n/broadcast <message>\n\n"
            f"👥 Total Users: {len(users_db.get('all_users', []))}",
            InlineKeyboardMarkup([[back_btn()]]),
        )

    # ── Lock bot ──
    elif action == "lock_bot":
        if not is_admin(uid):
            await send("❌ Admin only.")
            return
        locked = users_db.get("bot_locked", False)
        await send(
            f"🔒 Lock Bot\n━━━━━━━━━━━━━━━━━\n"
            f"Status: {'🔒 Locked' if locked else '🔓 Unlocked'}\n"
            f"Subscribed users: {len(users_db.get('subscribed', []))}\n\n"
            f"Lock ON → sirf subscribed users access kar sakte hain.",
            InlineKeyboardMarkup([
                [cb("🔓 Unlock Now" if locked else "🔒 Lock Now", "toggle_lock")],
                [back_btn()],
            ]),
        )

    # ── Running bots ──
    elif action == "running_all":
        user_bots = {
            bid: info for bid, info in running_bots.items()
            if is_admin(uid) or int(info.get("uploaded_by", -1)) == uid
        }
        if not user_bots:
            await send(
                "🔢 Running Code\n━━━━━━━━━━━━━━━━━\nKoi bot nahi chal raha.",
                InlineKeyboardMarkup([[back_btn()]]),
            )
            return
        text = "🔢 Running Bots\n━━━━━━━━━━━━━━━━━\n\n"
        for bid, info in user_bots.items():
            st  = get_status(bid)
            em  = "🟢" if st == "running" else "🔴"
            pid = info.get("pid", "N/A")
            up  = cpu_u = mem_u = "N/A"
            if st == "running":
                if info.get("start_time"):
                    up = fmt_uptime(int(time.time() - info["start_time"]))
                try:
                    p     = psutil.Process(int(pid))
                    cpu_u = f"{p.cpu_percent(interval=0.1):.1f}%"
                    mem_u = f"{p.memory_info().rss/1024**2:.1f} MB"
                except Exception:
                    pass
            text += (
                f"{em} {info['name']}\n"
                f"   PID: {pid} | ⏱ {up}\n"
                f"   CPU: {cpu_u} | RAM: {mem_u}\n\n"
            )
        await send(text, InlineKeyboardMarkup([[back_btn()]]))

    # ── Admin panel ──
    elif action == "admin_panel":
        if not is_admin(uid):
            await send("❌ Admin only.")
            return
        total = len(running_bots)
        run_c = sum(1 for bid in running_bots if get_status(bid) == "running")
        await send(
            f"👑 Admin Panel\n━━━━━━━━━━━━━━━━━\n"
            f"Admin ID: {ADMIN_ID}\n\n"
            f"📊 Bots: {total} (🟢 {run_c})\n"
            f"👥 Users: {len(users_db.get('all_users', []))} | ✅ {len(users_db.get('subscribed', []))}\n"
            f"🔒 Lock: {'ON' if users_db.get('bot_locked') else 'OFF'}\n"
            f"📦 Log Channel: {LOG_CHANNEL}\n\n"
            f"Commands:\n"
            f"/broadcast /addsub /removesub\n"
            f"/lock /unlock /setchannel /setowner /listusers",
            InlineKeyboardMarkup([
                [cb("📣 Broadcast", "broadcast_info"), cb("🎊 Subs",    "subscriptions")],
                [cb("🔒 Lock Bot",  "lock_bot"),       cb("🔢 Running", "running_all")],
                [back_btn()],
            ]),
        )

    # ── Contact owner ──
    elif action == "contact_owner":
        owner = users_db.get("owner_username", "")
        kb    = []
        if owner:
            kb.append([url_btn(f"💬 Message @{owner} ↗", f"https://t.me/{owner}")])
        kb.append([back_btn()])
        await send(
            "👻 Contact Owner\n━━━━━━━━━━━━━━━━━\n"
            + (f"@{owner} se contact karo" if owner else "Owner set nahi.\n/setowner @username"),
            InlineKeyboardMarkup(kb),
        )

# ══════════════════════════════════════════════════════════════
#  FILE UPLOAD HANDLER
# ══════════════════════════════════════════════════════════════

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)
    if not is_subscribed(user.id):
        await update.message.reply_text("🔒 Access denied.")
        return

    doc   = update.message.document
    fname = doc.file_name or "unknown"
    ext   = Path(fname).suffix.lower()

    if ext not in (".py", ".zip"):
        await update.message.reply_text(
            "❌ Unsupported File!\n\n"
            "✅ Accepted:\n"
            "  🐍 .py — Single Python bot\n"
            "  📦 .zip — ZIP package (main.py inside)\n\n"
            "🌐 Language: Python 3.11 only"
        )
        return

    msg = await update.message.reply_text("⏳ Uploading... Please wait")

    try:
        file_obj = await doc.get_file()
        # Include user_id in bot_id so it's unique per user
        bot_id   = f"bot_{int(time.time())}_{user.id}"
        name     = Path(fname).stem

        if ext == ".py":
            bot_path = BOTS_DIR / bot_id
            bot_path.mkdir(exist_ok=True)
            dest = bot_path / fname
            await file_obj.download_to_drive(str(dest))
            running_bots[bot_id] = {
                "name": name, "type": "single", "path": str(dest),
                "process": None, "pid": None, "start_time": None,
                "upload_time": time.time(), "auto_restart": True,
                "uploaded_by": user.id, "was_running": False,
            }
        else:
            zip_path = UPLOADS_DIR / f"{bot_id}.zip"
            await file_obj.download_to_drive(str(zip_path))
            extract  = BOTS_DIR / bot_id
            extract.mkdir(exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(str(extract))
            zip_path.unlink(missing_ok=True)
            running_bots[bot_id] = {
                "name": name, "type": "zip", "path": str(extract),
                "process": None, "pid": None, "start_time": None,
                "upload_time": time.time(), "auto_restart": True,
                "uploaded_by": user.id, "was_running": False,
            }

        save_state()
        await _log_to_channel(ctx, update, bot_id, name, ext.upper(), doc.file_size or 0)

        await msg.edit_text(
            f"✅ Bot Uploaded Successfully!\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📛 Name: {name}\n"
            f"🆔 ID: {bot_id}\n"
            f"📄 Type: {'Single .py' if ext == '.py' else 'ZIP Package'}\n"
            f"📏 Size: {(doc.file_size or 0) / 1024:.1f} KB\n"
            f"🔄 Auto-Restart: ✅ 24/7\n"
            f"⚠️ Crash Notify: ✅ Auto\n"
            f"🛡️ Control: Sirf tum\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"▶️ Ab start karo:",
            reply_markup=InlineKeyboardMarkup([
                [
                    cb("🚀 Start Now",   f"start_{bot_id}"),
                    cb("💎 All My Bots", "check_files"),
                ]
            ]),
        )
    except Exception as e:
        await msg.edit_text(f"❌ Upload Failed!\n\n{e}")

# ══════════════════════════════════════════════════════════════
#  CHANNEL LOGGER
# ══════════════════════════════════════════════════════════════

async def _log_to_channel(context, update, bot_id, name, ftype, file_size):
    user     = update.effective_user
    username = f"@{user.username}" if user.username else f"{user.first_name} (id:{user.id})"
    caption  = (
        f"📦 New Bot Uploaded!\n\n"
        f"👤 User: {username} ({user.id})\n"
        f"📛 Name: {name}\n"
        f"🆔 ID: {bot_id}\n"
        f"📄 Type: {ftype}\n"
        f"📏 Size: {file_size/1024:.1f} KB\n"
        f"🕐 Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    try:
        await context.bot.forward_message(
            chat_id=LOG_CHANNEL,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        await context.bot.send_message(chat_id=LOG_CHANNEL, text=caption)
    except Exception as e:
        print(f"[LOG_CHANNEL] {e}", flush=True)

# ══════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user.id)
    await update.message.reply_text(
        "📖 Commands\n"
        "━━━━━━━━━━━━━━━━━\n"
        "/start — Main menu\n"
        "/list — Mere hosted bots\n"
        "/logs <id> — View bot logs\n"
        "/stats — Statistics\n"
        "/help — Help\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📤 Supported:\n"
        "  🐍 .py — Single Python file\n"
        "  📦 .zip — Multi-file package\n\n"
        "🌐 Language: Python 3.11\n"
        "🔄 24/7 Auto-restart!\n"
        "🛡️ Sirf apna bot control karo"
    )


async def list_bots_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)
    if not is_subscribed(user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    text, markup = bots_list_view(user.id)
    await update.message.reply_text(text, reply_markup=markup)


async def logs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_subscribed(user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /logs <bot_id>")
        return
    bid = ctx.args[0]
    if not owns_bot(user.id, bid):
        await update.message.reply_text("🚫 Sirf apna bot ke logs dekh sakte ho!")
        return
    log_file = BOTS_DIR / f"{bid}.log"
    if not log_file.exists():
        await update.message.reply_text("❌ No logs found.")
        return
    lines   = log_file.read_text(errors="replace").splitlines()
    last    = "\n".join(lines[-30:]) if len(lines) > 30 else "\n".join(lines)
    snippet = (last.strip() or "(empty)")
    if len(snippet) > 3800:
        snippet = "..." + snippet[-3800:]
    await update.message.reply_text(f"📋 Logs — {bid}:\n\n{snippet}")


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    register_user(user.id)
    total = len(running_bots)
    run_c = sum(1 for bid in running_bots if get_status(bid) == "running")
    mem   = psutil.virtual_memory()
    cpu   = psutil.cpu_percent(interval=0.5)
    await update.message.reply_text(
        f"📊 Statistics\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🤖 Bots: {total} (🟢 {run_c})\n"
        f"💻 CPU: {cpu:.1f}%\n"
        f"🧠 RAM: {mem.used/1024**2:.1f} / {mem.total/1024**2:.1f} MB\n\n"
        f"👥 Users: {len(users_db.get('all_users', []))}\n"
        f"🔒 Locked: {'Yes' if users_db.get('bot_locked') else 'No'}\n"
        f"🔄 Watchdog: Active 24/7"
    )


async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <msg>")
        return
    text   = " ".join(ctx.args)
    ok = fail = 0
    for uid in users_db.get("all_users", []):
        try:
            await ctx.bot.send_message(
                chat_id=int(uid),
                text=f"📣 Broadcast\n━━━━━━━━━━━━━━━━━\n{text}",
            )
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"📣 Done!\n✅ Sent: {ok} | ❌ Failed: {fail}")


async def addsub_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /addsub <user_id>")
        return
    uid = ctx.args[0]
    if uid not in users_db["subscribed"]:
        users_db["subscribed"].append(uid)
        save_users()
    await update.message.reply_text(f"✅ {uid} subscribed.")


async def removesub_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /removesub <user_id>")
        return
    uid = ctx.args[0]
    if uid in users_db["subscribed"]:
        users_db["subscribed"].remove(uid)
        save_users()
    await update.message.reply_text(f"✅ {uid} unsubscribed.")


async def lock_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    users_db["bot_locked"] = True
    save_users()
    await update.message.reply_text("🔒 Bot locked!")


async def unlock_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    users_db["bot_locked"] = False
    save_users()
    await update.message.reply_text("🔓 Bot unlocked!")


async def setowner_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /setowner <username>")
        return
    users_db["owner_username"] = ctx.args[0].lstrip("@")
    save_users()
    await update.message.reply_text(f"✅ Owner: @{users_db['owner_username']}")


async def setchannel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /setchannel @channel")
        return
    users_db["updates_channel"] = ctx.args[0]
    save_users()
    await update.message.reply_text(f"✅ Updates channel: {users_db['updates_channel']}")


async def listusers_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    all_u = users_db.get("all_users", [])
    subs  = users_db.get("subscribed", [])
    text  = f"👥 Users ({len(all_u)}):\n━━━━━━━━━━━━━━━━━\n"
    for uid in all_u[-50:]:
        text += f"{'✅' if uid in subs else '👤'} {uid}\n"
    if len(all_u) > 50:
        text += f"\n…+{len(all_u)-50} more"
    await update.message.reply_text(text)

# ══════════════════════════════════════════════════════════════
#  POST-INIT
# ══════════════════════════════════════════════════════════════

async def post_init(application: Application):
    global BOT_USERNAME
    me           = await application.bot.get_me()
    BOT_USERNAME = me.username
    print(f"✅ Bot: @{BOT_USERNAME}", flush=True)
    await application.bot.set_my_commands([
        ("start",   "Main menu"),
        ("list",    "Mere hosted bots"),
        ("logs",    "Bot logs dekho"),
        ("stats",   "Statistics"),
        ("help",    "Help"),
    ])

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set!", file=sys.stderr)
        sys.exit(1)

    load_state()
    load_users()

    print(f"✅ Admin: {ADMIN_ID}", flush=True)
    print(f"✅ Log Channel: {LOG_CHANNEL}", flush=True)
    print(f"✅ Bots loaded: {list(running_bots.keys())}", flush=True)
    print(f"✅ Users: {len(users_db.get('all_users', []))}", flush=True)

    started = auto_start_bots()
    print(f"✅ Auto-started {started} bot(s)", flush=True)

    threading.Thread(target=watchdog_loop, daemon=True).start()
    print("✅ 24/7 Watchdog active!", flush=True)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("list",       list_bots_cmd))
    app.add_handler(CommandHandler("logs",       logs_cmd))
    app.add_handler(CommandHandler("stats",      stats_cmd))
    app.add_handler(CommandHandler("broadcast",  broadcast_cmd))
    app.add_handler(CommandHandler("addsub",     addsub_cmd))
    app.add_handler(CommandHandler("removesub",  removesub_cmd))
    app.add_handler(CommandHandler("lock",       lock_cmd))
    app.add_handler(CommandHandler("unlock",     unlock_cmd))
    app.add_handler(CommandHandler("setowner",   setowner_cmd))
    app.add_handler(CommandHandler("setchannel", setchannel_cmd))
    app.add_handler(CommandHandler("listusers",  listusers_cmd))

    # Reply keyboard text buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # File uploads
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(btn))

    # Crash notify job every 15s
    app.job_queue.run_repeating(send_crash_notifications, interval=15, first=10)

    print("🔥 EXHAUST HOSTING — Bot polling 24/7...", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
