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

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
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
        if not is_subscribed(user.id):
            await update.message.reply_text("🔒 Access denied.")
            return
        if not running_bots:
            await update.message.reply_text(
                "🔢 *Running Code*\n━━━━━━━━━━━━━━━━━\nKoi bot nahi chal raha.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
            )
            return
        text = "🔢 *Running Bots*\n━━━━━━━━━━━━━━━━━\n\n"
        for bid, info in running_bots.items():
            st = get_status(bid)
            em = "🟢" if st == "running" else "🔴"
            pid = info.get("pid", "N/A")
            up = cpu_u = mem_u = "N/A"
            if st == "running":
                if info.get("start_time"):
                    s = int(time.time() - info["start_time"])
                    up = f"{s//3600}h {(s%3600)//60}m {s%60}s"
                try:
                    p = psutil.Process(int(pid))
                    cpu_u = f"{p.cpu_percent(interval=0.1):.1f}%"
                    mem_u = f"{p.memory_info().rss/1024**2:.1f} MB"
                except Exception:
                    pass
            text += (
                f"{em} *{info['name']}*\n"
                f"   PID: `{pid}` | ⏱ `{up}`\n"
                f"   CPU: `{cpu_u}` | RAM: `{mem_u}`\n\n"
            )
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[au("menu", "🔙  Back  ↗")]]),
        )

    # ── Admin panel ──
    elif action == "admin_panel":
        if not is_admin(user.id):
            await update.message.reply_text("❌ Admin only.")
            return
        total = len(running_bots)
        run_c = sum(1 for bid in running_bots if get_status(bid) == "running")
        kb = InlineKeyboardMarkup([
            [
                au("broadcast_info", "📣 Broadcast  ↗"),
                au("subscriptions",  "🎊 Subs  ↗"),
            ],
            [
                au("lock_bot",    "🔒 Lock Bot  ↗"),
                au("running_all", "🔢 Running  ↗"),
            ],
            [au("menu", "🔙  Back  ↗")],
        ])
        await update.message.reply_text(
            f"👑 *Admin Panel*\n━━━━━━━━━━━━━━━━━\n"
            f"ID: `{ADMIN_ID}`\n\n"
            f"📊 Bots: `{total}` (🟢 {run_c})\n"
            f"👥 Users: `{len(users_db.get('all_users', []))}` | ✅ `{len(users_db.get('subscribed', []))}`\n"
            f"🔒 Lock: `{'ON' if users_db.get('bot_locked') else 'OFF'}`\n"
            f"📦 Log Channel: `{LOG_CHANNEL}`\n\n"
            f"*Commands:*\n"
            f"`/broadcast` `/addsub` `/removesub`\n"
            f"`/lock` `/unlock` `/setchannel` `/setowner` `/listusers`",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    # ── Contact owner ──
    elif action == "contact_owner":
        owner = users_db.get("owner_username", "")
        kb = []
        if owner:
            kb.append([InlineKeyboardButton(f"💬 Message @{owner}  ↗", url=f"https://t.me/{owner}")])
        kb.append([au("menu", "🔙  Back  ↗")])
        await update.message.reply_text(
            "👻 *Contact Owner*\n━━━━━━━━━━━━━━━━━\n"
            + (f"@{owner} se contact karo" if owner else "Owner set nahi.\n`/setowner @username`"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    # ── Fallback → main menu ──
    else:
        await update.message.reply_text(
            f"✨ *Hello {user.first_name}!*\n\n"
            "🤖 *TEAM XIKZON — Bot Hosting*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔥 Python bots host karo 24/7:\n"
            "   • 🐍 Single `.py` file bots\n"
            "   • 📦 Multi-file `.zip` bots\n\n"
            "⚡ Auto-restart on crash\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👇 Choose an option:",
            parse_mode="Markdown",
            reply_markup=get_menu(user.id),
        )


# ─── Other command handlers ───────────────────────────────────────────────────

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user.id)
    await update.message.reply_text(
        "📖 *Commands*\n"
        "━━━━━━━━━━━━━━━━━\n"
        "*/start* — Main menu\n"
        "*/list* — Hosted bots\n"
        "*/start\\_bot <id>* — Start bot\n"
        "*/stop\\_bot <id>* — Stop bot\n"
        "*/logs <id>* — View logs\n"
        "*/delete <id>* — Delete bot\n"
        "*/stats* — Statistics\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📤 *Supported:*\n"
        "  🐍 `.py` — Single Python file\n"
        "  📦 `.zip` — Multi-file package\n\n"
        "🌐 Language: Python 3.11\n"
        "🔄 24/7 Auto-restart!\n"
        "⚠️ Crash errors sent to you auto",
        parse_mode="Markdown",
    )


async def list_bots_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)
    if not is_subscribed(user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    if not running_bots:
        await update.message.reply_text("📂 Koi bot nahi.\n\nFile bhejo!")
        return
    text = "💎 *Hosted Bots*\n━━━━━━━━━━━━━━━━━\n\n"
    kb = []
    for bid, info in running_bots.items():
        st = get_status(bid)
        em = "🟢" if st == "running" else "🔴"
        text += f"{em} *{info['name']}* • `{bid}`\n\n"
        kb.append([
            InlineKeyboardButton("⏹ Stop" if st == "running" else "▶️ Start", callback_data=f"toggle_{bid}"),
            InlineKeyboardButton("📋 Logs", callback_data=f"logs_{bid}"),
            InlineKeyboardButton("🗑", callback_data=f"delete_{bid}"),
        ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)
    if not is_subscribed(user.id):
        await update.message.reply_text("🔒 Access denied.")
        return

    doc = update.message.document
    fname = doc.file_name or "unknown"
    ext = Path(fname).suffix.lower()

    if ext not in (".py", ".zip"):
        await update.message.reply_text(
            "❌ *Unsupported File Type!*\n\n"
            "✅ *Accepted:*\n"
            "  🐍 `.py` — Single Python bot\n"
            "  📦 `.zip` — ZIP package (main.py inside)\n\n"
            "🌐 Language: Python 3.11 only",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("⏳ *Uploading...* Please wait")

    try:
        file_obj = await doc.get_file()
        bot_id = f"bot_{int(time.time())}"
        name = Path(fname).stem

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
            extract = BOTS_DIR / bot_id
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

        # Log to channel
        await log_to_channel(ctx, update, bot_id, name, ext.upper(), doc.file_size or 0)

        await msg.edit_text(
            f"✅ *Bot Uploaded Successfully!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📛 Name: `{name}`\n"
            f"🆔 ID: `{bot_id}`\n"
            f"📄 Type: `{'Single .py' if ext == '.py' else 'ZIP Package'}`\n"
            f"📏 Size: `{(doc.file_size or 0) / 1024:.1f} KB`\n"
            f"🔄 Auto-Restart: ✅ 24/7\n"
            f"⚠️ Crash Notify: ✅ Auto\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"▶️ Start karo!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚀 Start Now", callback_data=f"toggle_{bot_id}"),
                    au("check_files", "💎 All Bots  ↗"),
                ]
            ]),
        )
    except Exception as e:
        await msg.edit_text(f"❌ *Upload Failed!*\n\n`{e}`", parse_mode="Markdown")


async def start_bot_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_subscribed(update.effective_user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/start_bot <bot_id>`", parse_mode="Markdown")
        return
    ok, msg = start_bot(ctx.args[0])
    await update.message.reply_text(f"{'✅' if ok else '❌'} {msg}")


async def stop_bot_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_subscribed(update.effective_user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/stop_bot <bot_id>`", parse_mode="Markdown")
        return
    ok, msg = stop_bot(ctx.args[0])
    await update.message.reply_text(f"{'✅' if ok else '❌'} {msg}")


async def logs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_subscribed(update.effective_user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/logs <bot_id>`", parse_mode="Markdown")
        return
    log_file = BOTS_DIR / f"{ctx.args[0]}.log"
    if not log_file.exists():
        await update.message.reply_text("❌ No logs found.")
        return
    lines = log_file.read_text(errors="replace").splitlines()
    last = "\n".join(lines[-30:]) if len(lines) > 30 else "\n".join(lines)
    await update.message.reply_text(
        f"📋 *Logs — {ctx.args[0]}:*\n\n```\n{last.strip() or '(empty)'}\n```",
        parse_mode="Markdown",
    )


async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_subscribed(update.effective_user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/delete <bot_id>`", parse_mode="Markdown")
        return
    bot_id = ctx.args[0]
    if bot_id not in running_bots:
        await update.message.reply_text("❌ Bot not found.")
        return
    stop_bot(bot_id)
    info = running_bots.pop(bot_id)
    p = Path(info["path"])
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.is_file():
        p.unlink(missing_ok=True)
        if p.parent != BOTS_DIR:
            shutil.rmtree(p.parent, ignore_errors=True)
    (BOTS_DIR / f"{bot_id}.log").unlink(missing_ok=True)
    save_state()
    await update.message.reply_text(f"✅ Bot `{bot_id}` deleted.", parse_mode="Markdown")


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id)
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
        f"🔒 Locked: `{'Yes' if users_db.get('bot_locked') else 'No'}`\n"
        f"🔄 Watchdog: `Active 24/7`",
        parse_mode="Markdown",
    )


async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/broadcast <msg>`", parse_mode="Markdown")
        return
    text = " ".join(ctx.args)
    ok = fail = 0
    for uid in users_db.get("all_users", []):
        try:
            await ctx.bot.send_message(
                chat_id=int(uid),
                text=f"📣 *Broadcast*\n━━━━━━━━━━━━━━━━━\n{text}",
                parse_mode="Markdown",
            )
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"📣 Done!\n✅ Sent: `{ok}` | ❌ Failed: `{fail}`", parse_mode="Markdown")


async def addsub_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/addsub <user_id>`", parse_mode="Markdown")
        return
    uid = ctx.args[0]
    if uid not in users_db["subscribed"]:
        users_db["subscribed"].append(uid)
        save_users()
    await update.message.reply_text(f"✅ `{uid}` subscribed.", parse_mode="Markdown")


async def removesub_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/removesub <user_id>`", parse_mode="Markdown")
        return
    uid = ctx.args[0]
    if uid in users_db["subscribed"]:
        users_db["subscribed"].remove(uid)
        save_users()
    await update.message.reply_text(f"✅ `{uid}` unsubscribed.", parse_mode="Markdown")


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
        await update.message.reply_text("Usage: `/setowner <username>`")
        return
    users_db["owner_username"] = ctx.args[0].lstrip("@")
    save_users()
    await update.message.reply_text(f"✅ Owner: @{users_db['owner_username']}")


async def setchannel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: `/setchannel @channel`")
        return
    users_db["updates_channel"] = ctx.args[0]
    save_users()
    await update.message.reply_text(f"✅ Updates channel: {users_db['updates_channel']}")


async def listusers_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    all_u = users_db.get("all_users", [])
    subs = users_db.get("subscribed", [])
    text = f"👥 *Users ({len(all_u)}):*\n━━━━━━━━━━━━━━━━━\n"
    for uid in all_u[-50:]:
        text += f"{'✅' if uid in subs else '👤'} `{uid}`\n"
    if len(all_u) > 50:
        text += f"\n…+{len(all_u)-50} more"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Channel Logger ───────────────────────────────────────────────────────────

async def log_to_channel(context, update, bot_id, name, ftype, file_size):
    user = update.effective_user
    username = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
    caption = (
        f"📦 *New Bot Uploaded!*\n\n"
        f"👤 User: {username} (`{user.id}`)\n"
        f"📛 Name: `{name}`\n"
        f"🆔 ID: `{bot_id}`\n"
        f"📄 Type: `{ftype}`\n"
        f"📏 Size: `{file_size/1024:.1f} KB`\n"
        f"🕐 Time: `{time.strftime('%Y-%m-%d %H:%M:%S UTC')}`"
    )
    try:
        await context.bot.forward_message(
            chat_id=LOG_CHANNEL,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        await context.bot.send_message(chat_id=LOG_CHANNEL, text=caption, parse_mode="Markdown")
    except Exception as e:
        print(f"[LOG_CHANNEL] {e}", flush=True)


# ─── Callback Handler (only for inline actions: toggle/logs/delete/lock) ──────

async def btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    await q.answer()
    data = q.data
    register_user(user.id)

    # ── Toggle lock ──
    if data == "toggle_lock":
        if not is_admin(user.id):
            await q.answer("❌ Admin only!", show_alert=True)
            return
        users_db["bot_locked"] = not users_db.get("bot_locked", False)
        save_users()
        locked = users_db["bot_locked"]
        await q.answer(f"{'🔒 Locked!' if locked else '🔓 Unlocked!'}", show_alert=True)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 Unlock" if locked else "🔒 Lock", callback_data="toggle_lock")],
            [au("menu", "🔙  Back  ↗")],
        ])
        try:
            await q.edit_message_text(
                f"🔒 *Lock Bot*\n━━━━━━━━━━━━━━━━━\n"
                f"Status: `{'🔒 Locked' if locked else '🔓 Unlocked'}`",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception:
            pass

    # ── Toggle bot start/stop ──
    elif data.startswith("toggle_"):
        bid = data[len("toggle_"):]
        if not is_subscribed(user.id):
            await q.answer("🔒 Access denied!", show_alert=True)
            return
        st = get_status(bid)
        if st == "running":
            running_bots[bid]["auto_restart"] = False
            ok, msg_t = stop_bot(bid)
        else:
            running_bots[bid]["auto_restart"] = True
            ok, msg_t = start_bot(bid)
        await q.answer(msg_t, show_alert=True)

        text = "💎 *Hosted Bots*\n━━━━━━━━━━━━━━━━━\n\n"
        kb = []
        for b2, inf in running_bots.items():
            s2 = get_status(b2)
            em = "🟢" if s2 == "running" else "🔴"
            text += f"{em} *{inf['name']}* • `{b2}`\n   {inf['type'].upper()}\n\n"
            kb.append([
                InlineKeyboardButton("⏹ Stop" if s2 == "running" else "▶️ Start", callback_data=f"toggle_{b2}"),
                InlineKeyboardButton("📋 Logs", callback_data=f"logs_{b2}"),
                InlineKeyboardButton("🗑", callback_data=f"delete_{b2}"),
            ])
        kb.append([au("menu", "🔙  Back  ↗")])
        try:
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            pass

    # ── View logs ──
    elif data.startswith("logs_"):
        bid = data[len("logs_"):]
        log_f = BOTS_DIR / f"{bid}.log"
        kb = [[au("check_files", "🔙  Back  ↗")]]
        if not log_f.exists():
            await q.edit_message_text("❌ No logs found.", reply_markup=InlineKeyboardMarkup(kb))
            return
        lines = log_f.read_text(errors="replace").splitlines()
        last = "\n".join(lines[-20:]) if len(lines) > 20 else "\n".join(lines)
        try:
            await q.edit_message_text(
                f"📋 *Logs — {bid}:*\n\n```\n{last.strip() or '(empty)'}\n```",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        except Exception:
            pass

    # ── Delete confirmation ──
    elif data.startswith("delete_"):
        bid = data[len("delete_"):]
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_del_{bid}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_del"),
            ]
        ])
        try:
            await q.edit_message_text(
                f"⚠️ *Delete Bot?*\n\n`{bid}` permanently delete karna chahte ho?",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception:
            pass

    elif data == "cancel_del":
        try:
            await q.edit_message_text("❌ Cancelled.", reply_markup=InlineKeyboardMarkup([[au("check_files", "🔙 Back  ↗")]]))
        except Exception:
            pass

    elif data.startswith("confirm_del_"):
        bid = data[len("confirm_del_"):]
        if bid in running_bots:
            stop_bot(bid)
            info = running_bots.pop(bid)
            p = Path(info["path"])
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                p.unlink(missing_ok=True)
                if p.parent != BOTS_DIR:
                    shutil.rmtree(p.parent, ignore_errors=True)
            (BOTS_DIR / f"{bid}.log").unlink(missing_ok=True)
            save_state()
        await q.answer("✅ Deleted!", show_alert=True)
        try:
            await q.edit_message_text(
                "✅ Bot deleted!",
                reply_markup=InlineKeyboardMarkup([[au("check_files", "💎 All Bots  ↗")]]),
            )
        except Exception:
            pass


# ─── Post-init: fetch bot username ───────────────────────────────────────────

async def post_init(application: Application):
    global BOT_USERNAME
    me = await application.bot.get_me()
    BOT_USERNAME = me.username
    print(f"✅ Bot username: @{BOT_USERNAME}", flush=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set!", file=sys.stderr)
        sys.exit(1)

    load_state()
    load_users()

    print(f"✅ Bots: {list(running_bots.keys())}", flush=True)
    print(f"✅ Admin: {ADMIN_ID}", flush=True)
    print(f"✅ Log Channel: {LOG_CHANNEL}", flush=True)
    print(f"✅ Web App: {WEBAPP_URL}", flush=True)
    print(f"✅ Users: {len(users_db.get('all_users', []))}", flush=True)

    # Auto-start previously running bots
    started = auto_start_bots()
    print(f"✅ Auto-started {started} bot(s)", flush=True)

    threading.Thread(target=watchdog_loop, daemon=True).start()
    print("✅ 24/7 Watchdog active!", flush=True)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)   # fetch BOT_USERNAME before polling
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("list", list_bots_cmd))
    app.add_handler(CommandHandler("start_bot", start_bot_cmd))
    app.add_handler(CommandHandler("stop_bot", stop_bot_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addsub", addsub_cmd))
    app.add_handler(CommandHandler("removesub", removesub_cmd))
    app.add_handler(CommandHandler("lock", lock_cmd))
    app.add_handler(CommandHandler("unlock", unlock_cmd))
    app.add_handler(CommandHandler("setowner", setowner_cmd))
    app.add_handler(CommandHandler("setchannel", setchannel_cmd))
    app.add_handler(CommandHandler("listusers", listusers_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(btn))

    # Send crash notifications every 15s
    app.job_queue.run_repeating(send_crash_notifications, interval=15, first=10)

    print("🤖 Bot 24/7 polling...", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
