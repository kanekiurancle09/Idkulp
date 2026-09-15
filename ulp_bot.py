import logging
import os
import asyncio
import psutil
import hashlib
import time
import secrets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from dotenv import load_dotenv
from pyrogram import Client

# ── Config ──────────────────────────────────────────────────
load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN", "8781111418:AAGrTW3sprBAGvo-j382qntzwyuCs0hxm4U")
API_ID         = int(os.getenv("API_ID", "0") or "36481340")
API_HASH       = os.getenv("API_HASH", "de04ccc76166e670153bba4e037ad5de")
OWNER_IDS      = [8512332298]
OWNER_USERNAME = "@KANEKI_IDK"
# ────────────────────────────────────────────────────────────

from database_ulp import db_ulp

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
mt_client: Client | None = None

# ============================================================
# UTILS
# ============================================================

def fmt_size(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"

def fmt_eta(sec: float) -> str:
    if sec <= 0 or sec > 86400:
        return "—"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s" if m else f"{s}s"

def progress_bar(cur: int, total: int) -> str:
    if not total:
        return "⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛ ?.?%"
    pct = cur / total * 100
    done = int(pct / 10)
    return "🟩" * done + "⬜" * (10 - done) + f" {pct:.1f}%"

def proc_text(added, total, lps, elapsed):
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    eta = fmt_eta((total - added) / lps) if lps > 0 and total > added else "—"
    return (
        f"⚙️ <b>Processing...</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{progress_bar(added, total)}\n"
        f"✅ {added:,} added  ⏳ ETA: {eta}\n"
        f"🚀 {int(lps):,} lines/s  ⏱️ {int(elapsed)}s\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 CPU {cpu}%  🧠 RAM {ram}%"
    )

# ============================================================
# KEYBOARDS
# ============================================================

def main_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🔍 Search Combos", callback_data="search")]]
    if uid in OWNER_IDS:
        rows.append([
            InlineKeyboardButton("📊 Stats", callback_data="stats"),
            InlineKeyboardButton("⚙️ Admin", callback_data="admin")
        ])
    else:
        rows.append([InlineKeyboardButton("📊 Stats", callback_data="stats")])
    rows.append([InlineKeyboardButton("👑 Owner", url=f"https://t.me/{OWNER_USERNAME.lstrip('@')}")])
    return InlineKeyboardMarkup(rows)

def admin_kb(maint: bool) -> InlineKeyboardMarkup:
    lbl = "🔴 Maint OFF" if maint else "🟢 Maint ON"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats",         callback_data="a_stats"),
         InlineKeyboardButton("🖥️ VPS",           callback_data="a_vps")],
        [InlineKeyboardButton("📁 Upload File",   callback_data="a_upload"),
         InlineKeyboardButton("🔗 Upload Link",   callback_data="a_link")],
        [InlineKeyboardButton("📋 My Files",      callback_data="a_sources"),
         InlineKeyboardButton("🗑️ Delete File",   callback_data="a_del_src")],
        [InlineKeyboardButton("📢 Broadcast",     callback_data="a_broadcast"),
         InlineKeyboardButton(lbl,                callback_data="a_maint")],
        [InlineKeyboardButton("🚫 Ban",           callback_data="a_ban"),
         InlineKeyboardButton("✅ Unban",         callback_data="a_unban")],
        [InlineKeyboardButton("💀 Wipe ALL DB",   callback_data="a_wipe")],
        [InlineKeyboardButton("🔙 Menu",          callback_data="menu")]
    ])

back_admin = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin")]])
back_menu  = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu")]])

# ============================================================
# SMART EDIT
# ============================================================

async def sedit(update: Update, text: str, kb=None):
    q = update.callback_query
    try:
        if q.message.photo:
            await q.edit_message_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        try:
            await q.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass

# ============================================================
# /start
# ============================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name or "User"

    try:
        await db_ulp.add_user(uid, update.effective_user.username or "Unknown")
    except Exception:
        pass

    try:
        total = await db_ulp.get_total_count()
    except Exception:
        total = 0

    text = (
        f"╔══════════════════════╗\n"
        f"║   🔥 <b>ULP Pro Bot</b> 🔥   ║\n"
        f"╚══════════════════════╝\n\n"
        f"👋 Hey <b>{name}</b>!\n"
        f"🆔 ID: <code>{uid}</code>\n\n"
        f"📦 Database: <code>{total:,}</code> combos\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 {OWNER_USERNAME}"
    )
    await update.message.reply_text(text, reply_markup=main_kb(uid), parse_mode="HTML")

# ============================================================
# TEXT STATE MACHINE
# ============================================================

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    state = ctx.user_data.get("state")

    if uid not in OWNER_IDS and await db_ulp.get_maintenance():
        await update.message.reply_text("🚧 <b>Maintenance Mode ON</b>", parse_mode="HTML")
        return

    if state == "search":
        ctx.user_data["query"] = text
        ctx.user_data["state"] = None
        await update.message.reply_text(
            f"🔎 <b>Select Format</b>\n📌 Query: <code>{text}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 L:P",     callback_data="fmt_lp"),
                 InlineKeyboardButton("🌐 URL:L:P", callback_data="fmt_ulp")],
                [InlineKeyboardButton("🔙 Cancel",  callback_data="menu")]
            ]),
            parse_mode="HTML"
        )

    elif state == "del_src" and uid in OWNER_IDS:
        src_id = text.strip().upper()
        m = await update.message.reply_text(f"🗑️ Deleting <code>{src_id}</code>...", parse_mode="HTML")
        count = await db_ulp.delete_by_source(src_id)
        if count > 0:
            await m.edit_text(
                f"✅ <b>Deleted!</b>\n🆔 <code>{src_id}</code>\n🗑️ <code>{count:,}</code> ULPs removed",
                parse_mode="HTML"
            )
        else:
            await m.edit_text(f"❌ No entries for <code>{src_id}</code>", parse_mode="HTML")
        ctx.user_data["state"] = None

    elif state == "del" and uid in OWNER_IDS:
        m = await update.message.reply_text("🗑️ Deleting...", parse_mode="HTML")
        count = await db_ulp.delete_single_ulp(text)
        await m.edit_text(f"✅ Deleted <code>{count:,}</code> entries matching <code>{text}</code>", parse_mode="HTML")
        ctx.user_data["state"] = None

    elif state == "link" and uid in OWNER_IDS:
        m = await update.message.reply_text("⏳ Downloading from link...", parse_mode="HTML")
        src_id = "SRC_" + secrets.token_hex(4).upper()
        added = await db_ulp.add_ulp_from_url(text, source_id=src_id)
        await db_ulp.register_source(src_id, text[:50], added)
        await m.edit_text(
            f"✅ <b>Link Upload Done!</b>\n"
            f"📦 Added <code>{added:,}</code> lines\n"
            f"🆔 Source ID: <code>{src_id}</code>",
            parse_mode="HTML"
        )
        ctx.user_data["state"] = None

    elif state == "ban" and uid in OWNER_IDS:
        try:
            await db_ulp.ban_user(int(text), 1)
            await update.message.reply_text(f"🚫 Banned <code>{text}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Send a numeric Telegram ID", parse_mode="HTML")
        ctx.user_data["state"] = None

    elif state == "unban" and uid in OWNER_IDS:
        try:
            await db_ulp.ban_user(int(text), 0)
            await update.message.reply_text(f"✅ Unbanned <code>{text}</code>", parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Send a numeric Telegram ID", parse_mode="HTML")
        ctx.user_data["state"] = None

    elif state == "broadcast" and uid in OWNER_IDS:
        users = await db_ulp.get_all_users()
        sent = failed = 0
        m = await update.message.reply_text("📢 Broadcasting...", parse_mode="HTML")
        for u in users:
            try:
                await ctx.bot.copy_message(u, update.message.chat_id, update.message.message_id)
                sent += 1
            except Exception:
                failed += 1
        await m.edit_text(f"📢 Done! ✅ {sent}  ❌ {failed}", parse_mode="HTML")
        ctx.user_data["state"] = None

# ============================================================
# DOCUMENT UPLOAD
# ============================================================

async def on_doc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in OWNER_IDS:
        return
    if ctx.user_data.get("state") != "upload":
        return

    import aiohttp
    doc = update.message.document
    file_size = doc.file_size or 0
    filename  = doc.file_name or "upload.txt"

    # Bot API direct download is limited; fallback to MTProto client for large files.
    MAX = 20 * 1024 * 1024

    source_id = "SRC_" + secrets.token_hex(4).upper()
    path = f"up_{uid}.txt"
    start = time.time()

    msg = await update.message.reply_text(
        f"📥 <b>Downloading...</b>\n"
        f"📁 {filename}  ({fmt_size(file_size)})\n"
        f"🆔 Source ID: <code>{source_id}</code>",
        parse_mode="HTML"
    )

    try:
        downloaded = 0
        last_edit = 0
        spd_bytes = 0
        spd_ts = time.time()
        speed = 0.0
        total_size = file_size

        async def maybe_edit_progress():
            nonlocal last_edit, speed
            now = time.time()
            if now - last_edit < 2.5:
                return
            bar = progress_bar(downloaded, total_size)
            eta = fmt_eta((total_size - downloaded) / speed) if speed > 0 else "—"
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            try:
                await msg.edit_text(
                    f"📥 <b>Downloading...</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{bar}\n"
                    f"📦 {fmt_size(downloaded)} / {fmt_size(total_size)}\n"
                    f"🚀 {fmt_size(int(speed))}/s  ⏳ {eta}\n"
                    f"🔥 CPU {cpu}%  🧠 RAM {ram}%",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            last_edit = now

        if file_size > MAX:
            if mt_client is None:
                await msg.edit_text(
                    "❌ Large upload mode is OFF.\n"
                    "Set `API_ID` and `API_HASH` in `.env`, then restart bot.",
                    parse_mode="HTML"
                )
                ctx.user_data["state"] = None
                return

            last_current = 0

            async def mt_progress(current: int, total: int, *_):
                nonlocal downloaded, total_size, spd_bytes, spd_ts, speed, last_current
                downloaded = current
                total_size = total or file_size
                delta = max(current - last_current, 0)
                spd_bytes += delta
                last_current = current
                now = time.time()
                if now - spd_ts >= 1.0:
                    speed = spd_bytes / max((now - spd_ts), 0.001)
                    spd_bytes = 0
                    spd_ts = now
                await maybe_edit_progress()

            await msg.edit_text(
                f"📥 <b>Downloading via MTProto...</b>\n"
                f"📁 {filename} ({fmt_size(file_size)})\n"
                f"🆔 Source ID: <code>{source_id}</code>",
                parse_mode="HTML"
            )
            await mt_client.download_media(doc.file_id, file_name=path, progress=mt_progress)
        else:
            tg_file = await ctx.bot.get_file(doc.file_id)
            async with aiohttp.ClientSession() as session:
                async with session.get(tg_file.file_path, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    total_size = int(resp.headers.get("content-length", file_size))
                    with open(path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(128 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            spd_bytes += len(chunk)
                            now = time.time()
                            if now - spd_ts >= 1.0:
                                speed = spd_bytes / max((now - spd_ts), 0.001)
                                spd_bytes = 0
                                spd_ts = now
                            await maybe_edit_progress()
    except Exception as e:
        await msg.edit_text(f"❌ Download error: <code>{e}</code>", parse_mode="HTML")
        return

    dl_time = time.time() - start
    await msg.edit_text(
        f"✅ Downloaded ({dl_time:.1f}s)  ⚙️ Processing...",
        parse_mode="HTML"
    )

    # Count lines
    total_lines = sum(1 for _ in open(path, "r", encoding="utf-8", errors="ignore"))

    # Process
    added = 0
    batch = []
    ps = time.time()
    le = time.time()
    lsa = 0
    lst = time.time()
    lps = 0.0

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip().split(":")
            if len(p) >= 3:
                h = hashlib.md5(f"{p[0]}:{p[1]}:{p[2]}".encode()).hexdigest()
                batch.append((p[0], p[1], p[2], h, source_id))
            if len(batch) >= 10000:
                added += await db_ulp.add_ulps_batch(batch)
                batch = []
                now = time.time()
                if now - lst >= 1.0:
                    lps = (added - lsa) / (now - lst)
                    lsa = added
                    lst = now
                if now - le >= 3.0:
                    try:
                        await msg.edit_text(
                            proc_text(added, total_lines, lps, now - ps),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    le = now
    if batch:
        added += await db_ulp.add_ulps_batch(batch)

    try:
        os.remove(path)
    except Exception:
        pass

    await db_ulp.register_source(source_id, filename, added)
    ctx.user_data["state"] = None
    total_time = int(time.time() - start)

    await msg.edit_text(
        f"✅ <b>Upload Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 {filename}\n"
        f"✅ Added  : <code>{added:,}</code> lines\n"
        f"📄 Total  : <code>{total_lines:,}</code>\n"
        f"♻️ Dupes  : <code>{total_lines - added:,}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Source ID: <code>{source_id}</code>\n"
        f"⏱️ Total: <code>{total_time}s</code>",
        parse_mode="HTML"
    )

# ============================================================
# CALLBACKS
# ============================================================

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data
    logger.info("callback uid=%s data=%s", uid, data)
    await q.answer()

    try:
        maint = await db_ulp.get_maintenance()
        akb = admin_kb(maint)

        if data == "menu":
            ctx.user_data["state"] = None
            total = await db_ulp.get_total_count()
            await sedit(update,
                f"╔══════════════════════╗\n"
                f"║   🔥 <b>ULP Pro Bot</b> 🔥   ║\n"
                f"╚══════════════════════╝\n\n"
                f"📦 Database: <code>{total:,}</code> combos\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💎 {OWNER_USERNAME}",
                main_kb(uid)
            )

        elif data == "stats":
            total = await db_ulp.get_total_count()
            s = await db_ulp.get_bot_stats()
            await sedit(update,
                f"📊 <b>Stats</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 ULPs  : <code>{total:,}</code>\n"
                f"👥 Users : <code>{s['users']:,}</code>",
                back_menu
            )

        elif data == "search":
            ctx.user_data["state"] = "search"
            await sedit(update,
                "🔍 <b>Search Combos</b>\n\nSend domain or keyword:\n<i>e.g. netflix.com, gmail</i>",
                back_menu
            )

        elif data == "admin":
            if uid not in OWNER_IDS:
                await q.answer("No access!", show_alert=True)
                return
            s = await db_ulp.get_bot_stats()
            maint_str = "🔴 ON" if maint else "🟢 OFF"
            await sedit(update,
                f"⚙️ <b>Admin Panel</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 ULPs  : <code>{s['ulps']:,}</code>\n"
                f"👥 Users : <code>{s['users']:,}</code>\n"
                f"🚧 Maint : {maint_str}",
                akb
            )

        elif data == "a_stats":
            if uid not in OWNER_IDS: return
            s = await db_ulp.get_bot_stats()
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            await sedit(update,
                f"📊 <b>Full Stats</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 ULPs  : <code>{s['ulps']:,}</code>\n"
                f"👥 Users : <code>{s['users']:,}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔥 CPU   : <code>{cpu}%</code>\n"
                f"🧠 RAM   : <code>{ram.percent}% ({ram.used//(1024**3)}GB/{ram.total//(1024**3)}GB)</code>\n"
                f"💾 Disk  : <code>{disk.percent}% ({disk.used//(1024**3)}GB/{disk.total//(1024**3)}GB)</code>",
                akb
            )

        elif data == "a_vps":
            if uid not in OWNER_IDS: return
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            await sedit(update,
                f"🖥️ <b>VPS Status</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔥 CPU  : <code>{cpu}%</code>\n"
                f"🧠 RAM  : <code>{ram.percent}% ({ram.used//(1024**3)}GB/{ram.total//(1024**3)}GB)</code>\n"
                f"💾 Disk : <code>{disk.percent}% ({disk.used//(1024**3)}GB/{disk.total//(1024**3)}GB)</code>",
                akb
            )

        elif data == "a_upload":
            if uid not in OWNER_IDS: return
            ctx.user_data["state"] = "upload"
            await sedit(update,
                "📁 <b>File Upload</b>\n\n"
                "Send your .txt file (max 20MB)\n"
                "For larger files use 🔗 Upload Link",
                back_admin
            )

        elif data == "a_link":
            if uid not in OWNER_IDS: return
            ctx.user_data["state"] = "link"
            await sedit(update,
                "🔗 <b>Link Upload</b>\n\n"
                "📦 <b>No size limit!</b>\n"
                "Send a direct download URL to your .txt file:",
                back_admin
            )

        elif data == "a_sources":
            if uid not in OWNER_IDS: return
            sources = await db_ulp.get_all_sources()
            if not sources:
                await sedit(update, "📋 No uploaded files yet.", back_admin)
                return
            lines = ["📋 <b>Uploaded Files</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
            for s in sources:
                date = str(s["added_at"])[:10] if s["added_at"] else "?"
                lines.append(
                    f"🆔 <code>{s['source_id']}</code>\n"
                    f"   📁 {s['filename']}\n"
                    f"   📦 {s['ulp_count']:,} ULPs | 🗓 {date}"
                )
            await sedit(update, "\n\n".join(lines), back_admin)

        elif data == "a_del_src":
            if uid not in OWNER_IDS: return
            sources = await db_ulp.get_all_sources()
            if not sources:
                await sedit(update, "📋 No uploaded files found.", back_admin)
                return
            ctx.user_data["state"] = "del_src"
            lines = ["🗑️ <b>Delete by Source ID</b>\n\nAvailable:"]
            for s in sources:
                lines.append(f"• <code>{s['source_id']}</code> — {s['filename']} ({s['ulp_count']:,})")
            lines.append("\n📝 Send the Source ID:")
            await sedit(update, "\n".join(lines),
                InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin")]]))

        elif data == "a_broadcast":
            if uid not in OWNER_IDS: return
            ctx.user_data["state"] = "broadcast"
            await sedit(update, "📢 Send message to broadcast:", back_admin)

        elif data == "a_ban":
            if uid not in OWNER_IDS: return
            ctx.user_data["state"] = "ban"
            await sedit(update, "🚫 Send Telegram ID to ban:", back_admin)

        elif data == "a_unban":
            if uid not in OWNER_IDS: return
            ctx.user_data["state"] = "unban"
            await sedit(update, "✅ Send Telegram ID to unban:", back_admin)

        elif data == "a_maint":
            if uid not in OWNER_IDS: return
            new = not maint
            await db_ulp.set_maintenance(new)
            await sedit(update,
                f"🚧 Maintenance: {'🔴 ON' if new else '🟢 OFF'}",
                admin_kb(new)
            )

        elif data == "a_wipe":
            if uid not in OWNER_IDS: return
            await sedit(update,
                "⚠️ <b>Confirm wipe ALL combos?</b>\nCannot be undone!",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton("💀 YES WIPE", callback_data="a_wipe_ok"),
                     InlineKeyboardButton("🔙 Cancel",   callback_data="admin")]
                ])
            )

        elif data == "a_wipe_ok":
            if uid not in OWNER_IDS: return
            await db_ulp.clear_db()
            await sedit(update, "💀 <b>Database wiped!</b>", akb)

        elif data in ("fmt_lp", "fmt_ulp"):
            qry = ctx.user_data.get("query")
            if not qry:
                await sedit(update, "❌ Session expired. Search again.", main_kb(uid))
                return
            await sedit(update, f"🔎 Searching <code>{qry}</code>...", None)
            res = await db_ulp.search_ulps(qry)
            if not res:
                await sedit(update, f"❌ No results for <code>{qry}</code>", back_menu)
                return
            fname = f"res_{uid}.txt"
            with open(fname, "w", encoding="utf-8") as f:
                for r in res:
                    if data == "fmt_lp":
                        f.write(f"{r['login']}:{r['password']}\n")
                    else:
                        f.write(f"{r['url']}:{r['login']}:{r['password']}\n")
            cap = (
                f"✅ <b>Results</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔍 {qry}\n"
                f"📦 {len(res):,} lines | {'L:P' if data=='fmt_lp' else 'URL:L:P'}\n"
                f"💎 {OWNER_USERNAME}"
            )
            await q.message.reply_document(open(fname, "rb"), caption=cap, parse_mode="HTML")
            try:
                os.remove(fname)
            except Exception:
                pass

        else:
            await sedit(update, "⚠️ Unknown action. Please tap /start again.", main_kb(uid))

    except Exception as e:
        logger.error(f"Callback [{data}] error: {e}", exc_info=True)
        try:
            await q.answer("Button error. Try /start again.", show_alert=True)
        except Exception:
            pass

# ============================================================
# MAIN
# ============================================================

async def post_init(app):
    global mt_client
    await db_ulp.init_db()
    logger.info("Database ready.")
    if API_ID and API_HASH:
        try:
            mt_client = Client(
                "ulp_mt_ingest",
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=BOT_TOKEN,
                no_updates=True,
                workers=2
            )
            await mt_client.start()
            logger.info("MTProto ingest client started.")
        except Exception as e:
            mt_client = None
            logger.error("MTProto ingest client failed: %s", e)
            logger.warning("Large file direct upload disabled until valid API_ID/API_HASH.")
    else:
        logger.warning("API_ID/API_HASH missing. Large file direct upload disabled.")


async def post_shutdown(app):
    global mt_client
    if mt_client is not None:
        try:
            await mt_client.stop()
            logger.info("MTProto ingest client stopped.")
        except Exception:
            pass
        mt_client = None


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Global handler error: %s", context.error, exc_info=True)

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.Document.ALL, on_doc))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_error_handler(on_error)

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
