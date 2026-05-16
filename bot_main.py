#!/usr/bin/env python3
"""
TEAM XIKZON — Bot Hosting | 24/7 Python Bot Hosting
All menu buttons are URL buttons = BLUE in Telegram
"""

import os
import sys
import asyncio
import json
import zipfile
import shutil
import subprocess
import time
import signal
import threading
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
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

BOT_TOKEN    = os.environ.get("8738189905:AAH5i2SuVqQMHdkTSdkckm3lCVrsQXBRORY", "")
ADMIN_ID     = 7082733957
LOG_CHANNEL  = -1003608585339
BOT_USERNAME = ""   # Set at startup via get_me()

REPLIT_DEV_DOMAIN = os.environ.get("REPLIT_DEV_DOMAIN", "")
WEBAPP_URL = f"https://{REPLIT_DEV_DOMAIN}/api/webapp" if REPLIT_DEV_DOMAIN else ""

BOTS_DIR    = Path(__file__).parent / "bots"
UPLOADS_DIR = Path(__file__).parent / "uploads"
STATE_FILE  = Path(__file__).parent / "bots_state.json"
USERS_FILE  = Path(__file__).parent / "users_state.json"

BOTS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

running_bots: dict = {}
users_db: dict = {
    "all_users": [],
    "subscribed": [],
    "bot_locked": False,
    "owner_username": "",
    "updates_channel": "",
}
crash_notifications: list = []


# ─── URL Button Helper ────────────────────────────────────────────────────────

def au(action: str, label: str) -> InlineKeyboardButton:
    """Create a URL button (appears BLUE in Telegram) using bot deep link."""
    url = f"https://t.me/{BOT_USERNAME}?start={action}"
    return InlineKeyboardButton(label, url=url)


# ─── Persistence ──────────────────────────────────────────────────────────────

def load_state():
    global running_bots
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text())
        for bot_id, info in data.items():
            running_bots[bot_id] = {
                "name": info.get("name", bot_id),
                "type": info.get("type", "unknown"),
                "path": info.get("path", ""),
                "pid": None, "process": None,
                "start_time": None,
                "upload_time": info.get("upload_time"),
                "auto_restart": info.get("auto_restart", True),
                "uploaded_by": info.get("uploaded_by", 0),
                "was_running": info.get("was_running", False),
                "status": "stopped",
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
            "name": i["name"], "type": i["type"], "path": i["path"],
            "start_time": i.get("start_time"), "upload_time": i.get("upload_time"),
            "auto_restart": i.get("auto_restart", True),
            "uploaded_by": i.get("uploaded_by", 0),
            "was_running": get_status(bid) == "running",
        }
    STATE_FILE.write_text(json.dumps(data, indent=2))


def save_users():
    USERS_FILE.write_text(json.dumps(users_db, indent=2))


def register_user(uid: int):
    if str(uid) not in users_db["all_users"]:
        users_db["all_users"].append(str(uid))
        save_users()


# ─── Auth ─────────────────────────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


def is_subscribed(uid: int) -> bool:
    if is_admin(uid):
        return True
    if not users_db.get("bot_locked", False):
        return True
    return str(uid) in users_db.get("subscribed", [])


# ─── Process Management ───────────────────────────────────────────────────────

def get_status(bot_id: str) -> str:
    if bot_id not in running_bots:
        return "unknown"
    proc = running_bots[bot_id].get("process")
    if proc is not None:
        if proc.poll() is None:
            return "running"
        running_bots[bot_id]["process"] = None
        running_bots[bot_id]["pid"] = None
    return "stopped"


def start_bot(bot_id: str) -> tuple[bool, str]:
    if bot_id not in running_bots:
        return False, "Bot nahi mila."
    info = running_bots[bot_id]
    bot_path = Path(info["path"])
    if not bot_path.exists():
        return False, "Bot file delete ho gayi."
    if get_status(bot_id) == "running":
        return False, "Bot already chal raha hai."

    if info["type"] == "single":
        cmd = [sys.executable, str(bot_path)]
        cwd = str(bot_path.parent)
    elif info["type"] == "zip":
        main = bot_path / "main.py"
        if not main.exists():
            py = list(bot_path.glob("*.py"))
            if not py:
                return False, "main.py nahi mili ZIP mein."
            main = py[0]
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
        running_bots[bot_id].update(process=proc, pid=proc.pid, start_time=time.time())
        save_state()
        return True, f"Bot start! PID: {proc.pid}"
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


def get_last_error(bot_id: str, lines: int = 25) -> str:
    log_f = BOTS_DIR / f"{bot_id}.log"
    if not log_f.exists():
        return "(no logs found)"
    try:
        content = log_f.read_text(errors="replace").splitlines()
        last = content[-lines:] if len(content) > lines else content
        return "\n".join(last)
    except Exception as e:
        return f"(log read error: {e})"


def watchdog_loop():
    while True:
        try:
            for bot_id, info in list(running_bots.items()):
                if not info.get("auto_restart", True):
                    continue
                proc = info.get("process")
                if proc is not None and proc.poll() is not None:
                    exit_code = proc.returncode
                    print(f"[WATCHDOG] {bot_id} crashed (exit={exit_code}) → restarting", flush=True)
                    error_snippet = get_last_error(bot_id, 25)
                    user_id = info.get("uploaded_by", 0)
                    running_bots[bot_id].update(process=None, pid=None)
                    if user_id:
                        crash_notifications.append({
                            "user_id": int(user_id),
                            "bot_name": info["name"],
                            "bot_id": bot_id,
                            "exit_code": exit_code,
                            "error": error_snippet,
                        })
                    ok, msg = start_bot(bot_id)
                    print(f"[WATCHDOG] {msg}", flush=True)
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
        uid = notif.get("user_id")
        if not uid:
            continue
        error = notif.get("error", "").strip()
        if len(error) > 800:
            error = "..." + error[-800:]
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"⚠️ *Bot Crashed & Restarted!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📛 Bot: `{notif['bot_name']}`\n"
                    f"🆔 ID: `{notif['bot_id']}`\n"
                    f"💥 Exit Code: `{notif.get('exit_code', '?')}`\n"
                    f"🔄 Status: Auto-restarted ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📋 *Last Error Log:*\n"
                    f"```\n{error or '(no error output)'}\n```"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Full Logs", callback_data=f"logs_{notif['bot_id']}")]
                ]),
            )
        except Exception as e:
            print(f"[NOTIFY] Failed: {e}", flush=True)


# ─── Menus (ALL URL buttons = ALL BLUE) ───────────────────────────────────────

def user_menu() -> InlineKeyboardMarkup:
    ch = users_db.get("updates_channel", "")
    ch_btn = (
        InlineKeyboardButton("📣  Updates Channel  ↗", url=f"https://t.me/{ch.lstrip('@')}")
        if ch else
        au("updates_channel", "📣  Updates Channel  ↗")
    )
    rows = [
        [ch_btn],
        [
            au("upload_info",  "🚀  Upload File  ↗"),
            au("check_files",  "💎  Check Files  ↗"),
        ],
        [
            au("bot_speed",    "⚡  Bot Speed  ↗"),
            au("stats",        "📊  Statistics  ↗"),
        ],
        [
            au("subscriptions","🎊  Subscriptions  ↗"),
            au("running_all",  "🔢  Running Code  ↗"),
        ],
        [au("contact_owner",   "👻  Contact Owner  ↗")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_menu() -> InlineKeyboardMarkup:
    ch = users_db.get("updates_channel", "")
    locked = users_db.get("bot_locked", False)
    ch_btn = (
        InlineKeyboardButton("📣  Updates Channel  ↗", url=f"https://t.me/{ch.lstrip('@')}")
        if ch else
        au("updates_channel", "📣  Updates Channel  ↗")
    )
    rows = [
        [ch_btn],
        [
            au("upload_info",  "🚀  Upload File  ↗"),
            au("check_files",  "💎  Check Files  ↗"),
        ],
        [
            au("bot_speed",    "⚡  Bot Speed  ↗"),
            au("stats",        "📊  Statistics  ↗"),
        ],
        [
            au("subscriptions","🎊  Subscriptions  ↗"),
            au("broadcast_info","🎙  Broadcast  ↗"),
        ],
        [
            au("lock_bot",     f"{'🔒' if not locked else '🔓'}  Lock Bot  ↗"),
            au("running_all",  "🔢  Running Code  ↗"),
        ],
        [
            au("admin_panel",  "👑  Admin Panel  ↗"),
            au("contact_owner","👻  Contact Owner  ↗"),
        ],
    ]
    # Dashboard only for admin — WebApp button also BLUE
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🌐  Open Dashboard  ↗", web_app=WebAppInfo(url=WEBAPP_URL))])
    return InlineKeyboardMarkup(rows)


def get_menu(uid: int) -> InlineKeyboardMarkup:
    return admin_menu() if is_admin(uid) else user_menu()


def back_menu(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[au("menu", "🔙  Back to Menu  ↗")]])


# ─── /start handler (handles all deep-link actions) ──────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)

    if not is_subscribed(user.id):
        owner = users_db.get("owner_username", "")
        contact = f"\n\n💬 Contact: @{owner}" if owner else ""
        await update.message.reply_text(
            "🔒 *Bot Locked!*\n\nSirf subscribed users use kar sakte hain." + contact,
            parse_mode="Markdown",
        )
        return

    action = ctx.args[0] if ctx.args else "menu"

    # ── Main menu ──
    if action == "menu":
        await update.message.reply_text(
            f"✨ *Hello {user.first_name}!*\n\n"
            "🤖 *TEAM XIKZON — Bot Hosting*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 Python bots host karo 24/7:\n"
            "   • 🐍 Single `.py` file bots\n"
            "   • 📦 Multi-file `.zip` bots\n\n"
            "⚡ Auto-restart on crash\n"
            "📲 Crash errors auto-notify\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👇 Choose an option:",
            parse_mode="Markdown",
            reply_markup=get_menu(user.id),
        )

    # ── Updates channel ──
    elif action == "updates_channel":
        ch = users_db.get("updates_channel", "")
        kb = []
        if ch:
            kb.append([InlineKeyboardButton(f"📣 Join {ch}  ↗", url=f"https://t.me/{ch.lstrip('@')}")])
        kb.append([au("menu", "🔙  Back  ↗")])
        await update.message.reply_text(
            f"📣 *Updates Channel*\n━━━━━━━━━━━━━━━━━\n"
            + (f"Join: {ch}" if ch else "Abhi set nahi hai."),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    # ── Upload info ──
    elif action == "upload_info":
        if not is_subscribed(user.id):
            await update.message.reply_text("🔒 Access denied.")
            return
        await update.message.reply_text(
            "🚀 *Upload Your Bot*\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "🐍 *Single Python File (.py):*\n"
            "  • Direct `.py` file bhejo\n"
            "  • BOT\\_TOKEN file ke andar hona chahiye\n\n"
            "📦 *ZIP Package (.zip):*\n"
            "  • `.zip` mein sab files dalo\n"
            "  • Main entry: `main.py` (required)\n\n"
            "🌐 *Language:* Python 3.11 only\n"
            "🔄 *24/7 Auto-restart on crash!*\n"
            "⚠️ *Errors:* Auto-notify hoga\n\n"
            "📲 *File bhejo* ⬇️",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
        )

    # ── Check files ──
    elif action == "check_files":
        if not is_subscribed(user.id):
            await update.message.reply_text("🔒 Access denied.")
            return
        if not running_bots:
            await update.message.reply_text(
                "📂 *No Bots Hosted*\n\nFile upload karo!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[au("upload_info", "🚀 Upload File  ↗")]]),
            )
            return
        text = "💎 *Hosted Bots*\n━━━━━━━━━━━━━━━━━\n\n"
        kb = []
        for bid, info in running_bots.items():
            st = get_status(bid)
            em = "🟢" if st == "running" else "🔴"
            secs = int(time.time() - info["start_time"]) if st == "running" and info.get("start_time") else 0
            up = f" ⏱ {secs//3600}h {(secs%3600)//60}m" if secs > 0 else ""
            text += f"{em} *{info['name']}*{up}\n   `{bid}` • {info['type'].upper()}\n\n"
            kb.append([
                InlineKeyboardButton("⏹ Stop" if st == "running" else "▶️ Start", callback_data=f"toggle_{bid}"),
                InlineKeyboardButton("📋 Logs", callback_data=f"logs_{bid}"),
                InlineKeyboardButton("🗑", callback_data=f"delete_{bid}"),
            ])
        kb.append([au("menu", "🔙  Back  ↗")])
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    # ── Bot speed ──
    elif action == "bot_speed":
        t1 = time.time()
        await asyncio.sleep(0)
        lat = (time.time() - t1) * 1000
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        await update.message.reply_text(
            f"⚡ *Speed Test*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🏓 Latency: `{lat:.2f} ms`\n"
            f"💻 CPU: `{cpu:.1f}%`\n"
            f"🧠 Free RAM: `{mem.available/1024**2:.1f} MB`\n"
            f"🔄 Status: `24/7 Online ✅`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
        )

    # ── Stats ──
    elif action == "stats":
        total = len(running_bots)
        run_c = sum(1 for bid in running_bots if get_status(bid) == "running")
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.5)
        await update.message.reply_text(
            f"📊 *Statistics*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 Bots: `{total}` (🟢 {run_c})\n"
            f"💻 CPU: `{cpu:.1f}%`\n"
            f"🧠 RAM: `{mem.used/1024**2:.1f}` / `{mem.total/1024**2:.1f} MB`\n\n"
            f"👥 Users: `{len(users_db.get('all_users', []))}`\n"
            f"✅ Subscribed: `{len(users_db.get('subscribed', []))}`\n"
            f"🔒 Locked: `{'Yes' if users_db.get('bot_locked') else 'No'}`\n"
            f"🌐 Language: `Python 3.11`\n"
            f"🔄 Watchdog: `Active 24/7`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
        )

    # ── Subscriptions ──
    elif action == "subscriptions":
        subs = users_db.get("subscribed", [])
        locked = users_db.get("bot_locked", False)
        if is_admin(user.id):
            text = (
                f"🎊 *Subscriptions*\n━━━━━━━━━━━━━━━━━\n"
                f"🔒 Lock: `{'ON' if locked else 'OFF'}`\n"
                f"✅ Subscribed: `{len(subs)}`\n\n"
                f"*Admin Commands:*\n"
                f"`/addsub <id>` • `/removesub <id>`\n"
                f"`/lock` • `/unlock`"
            )
        else:
            st = "✅ Subscribed" if str(user.id) in subs else "❌ Not Subscribed"
            text = f"🎊 *Subscriptions*\n━━━━━━━━━━━━━━━━━\nYour status: {st}"
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
        )

    # ── Broadcast info ──
    elif action == "broadcast_info":
        if not is_admin(user.id):
            await update.message.reply_text("❌ Admin only.")
            return
        await update.message.reply_text(
            f"🎙 *Broadcast*\n━━━━━━━━━━━━━━━━━\n"
            f"Send to all users:\n`/broadcast <message>`\n\n"
            f"👥 Total Users: `{len(users_db.get('all_users', []))}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
        )

    # ── Lock bot ──
    elif action == "lock_bot":
        if not is_admin(user.id):
            await update.message.reply_text("❌ Admin only.")
            return
        locked = users_db.get("bot_locked", False)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔓 Unlock Now" if locked else "🔒 Lock Now",
                callback_data="toggle_lock",
            )],
            [au("menu", "🔙  Back  ↗")],
        ])
        await update.message.reply_text(
            f"🔒 *Lock Bot*\n━━━━━━━━━━━━━━━━━\n"
            f"Status: `{'🔒 Locked' if locked else '🔓 Unlocked'}`\n"
            f"Subscribed: `{len(users_db.get('subscribed', []))}`\n\n"
            f"Lock ON → sirf subscribed users access kar sakte hain.",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    # ── Running bots ──
    elif action == "running_all":
total = len(running_bots)
    running = sum(1 for bid in running_bots if get_status(bid) == "running")

    await update.message.reply_text(
        f"🔢 Running Bots\n\n"
        f"Total Bots: {total}\n"
        f"Running: {running}",
        reply_markup=InlineKeyboardMarkup([
            [au("menu", "🔙 Back ↗")]
        ])
    )
     