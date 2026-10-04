import asyncio

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

import os
import time
import shutil
import logging
import tempfile

from pyrogram import Client, filters, idle
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo,
)
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, PeerIdInvalid

import db
import downloader

logging.basicConfig(level=logging.INFO)

app = Client(
    "insta_bot",
    api_id=int(os.environ["API_ID"]),
    api_hash=os.environ["API_HASH"],
    bot_token=os.environ["BOT_TOKEN"],
    in_memory=True,
)

CAPTION = os.getenv("CAPTION", "✅ Downloaded")
sem = asyncio.Semaphore(3)      # max parallel downloads (server load)
last_req = {}                   # uid -> last request time
busy = set()                    # uids with active download
admin_state = {}                # admin uid -> "wait" | {"chat":..,"msg":..}
tasks = set()


# ---------------- Admin broadcast ----------------
def _waiting(_, __, m):
    return bool(m.from_user) and admin_state.get(m.from_user.id) == "wait"


waiting = filters.create(_waiting)


@app.on_message(filters.command("start") & filters.private)
async def start(c, m):
    await db.add_user(m.from_user)
    kb = None
    if m.from_user.id in db.ADMIN_IDS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Broadcast", callback_data="bc_start")]])
    await m.reply_text("👋 Instagram Reel ya Image ka link bhejo, main download kar dunga.", reply_markup=kb)


@app.on_message(filters.command("cancel") & filters.private)
async def cancel(c, m):
    admin_state.pop(m.from_user.id, None)
    await m.reply_text("Cancelled.")


@app.on_message(filters.private & waiting & ~filters.command(["start", "cancel"]), group=-1)
async def capture_post(c, m):
    uid = m.from_user.id
    admin_state[uid] = {"chat": m.chat.id, "msg": m.id}
    await m.copy(m.chat.id)  # preview, exactly as users will see it
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sabko bhejo", callback_data="bc_send")],
        [InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")],
    ])
    await m.reply_text("Upar preview h. Sabhi users ko bhejun?", reply_markup=kb)
    m.stop_propagation()


@app.on_callback_query(filters.regex("^bc_"))
async def bc_cb(c, q):
    uid = q.from_user.id
    if uid not in db.ADMIN_IDS:
        return await q.answer("Not allowed", show_alert=True)

    if q.data == "bc_start":
        admin_state[uid] = "wait"
        await q.message.reply_text("📢 Ab wo post bhejo jo sabko bhejna h (image + caption + link).\nCancel: /cancel")
        await q.answer()
    elif q.data == "bc_cancel":
        admin_state.pop(uid, None)
        await q.message.edit_text("❌ Broadcast cancel.")
        await q.answer()
    elif q.data == "bc_send":
        st = admin_state.get(uid)
        if not isinstance(st, dict):
            return await q.answer("Pehle post bhejo.", show_alert=True)
        admin_state.pop(uid, None)
        await q.message.edit_text("🚀 Broadcast shuru...")
        t = asyncio.create_task(run_broadcast(c, st["chat"], st["msg"], q.message))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        await q.answer()


async def run_broadcast(c, chat, msg_id, status):
    try:
        ids = await db.active_user_ids()
    except Exception as e:
        logging.exception("broadcast: user list failed")
        return await status.edit_text(f"❌ Users load nahi hue: {e}")
    if not ids:
        return await status.edit_text("❌ Koi active user nahi mila.")
    sent = failed = blocked = 0
    for i, uid in enumerate(ids, 1):
        try:
            await c.copy_message(uid, chat, msg_id)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await c.copy_message(uid, chat, msg_id)
                sent += 1
            except Exception:
                failed += 1
        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            blocked += 1
            await db.set_inactive(uid)
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)  # ~25 msg/sec
        if i % 100 == 0:
            try:
                await status.edit_text(f"🚀 Sending... {i}/{len(ids)}")
            except Exception:
                pass
    await status.edit_text(
        f"✅ Broadcast done\n\nTotal: {len(ids)}\nSent: {sent}\nBlocked: {blocked}\nFailed: {failed}"
    )


# ---------------- Downloader ----------------
async def send_files(m, files):
    files = files[:10]
    if len(files) == 1:
        path, kind = files[0]
        if kind == "video":
            await m.reply_video(path, caption=CAPTION, supports_streaming=True)
        else:
            await m.reply_photo(path, caption=CAPTION)
        return
    media = [InputMediaVideo(p) if k == "video" else InputMediaPhoto(p) for p, k in files]
    media[0].caption = CAPTION
    await m.reply_media_group(media)


@app.on_message(filters.private & filters.text & ~filters.command(["start", "cancel"]))
async def on_link(c, m):
    uid = m.from_user.id
    p = downloader.parse(m.text)
    if not p:
        return await m.reply_text("❌ Valid Instagram reel/post link bhejo.")

    await db.add_user(m.from_user)
    if await db.is_banned(uid):
        return

    if uid not in db.ADMIN_IDS:
        wait = db.COOLDOWN - (time.time() - last_req.get(uid, 0))
        if wait > 0:
            return await m.reply_text(f"⏳ {int(wait) + 1} sec baad try karo.")
        if await db.count_today(uid) >= db.DAILY_LIMIT:
            return await m.reply_text(f"🚫 Aaj ki limit ({db.DAILY_LIMIT}) khatam. Kal try karo.")

    if uid in busy:
        return await m.reply_text("⏳ Pehla download abhi chal raha h.")

    busy.add(uid)
    last_req[uid] = time.time()
    status = await m.reply_text("⏳ Downloading...")
    tmp = tempfile.mkdtemp()
    kind, link = p[0], p[2]
    try:
        async with sem:
            files = await asyncio.to_thread(downloader.download, m.text, tmp)
        await send_files(m, files)
        await status.delete()
        await db.log_usage(uid, link, kind, "success")
    except downloader.DlError as e:
        await status.edit_text(f"❌ {e}")
        await db.log_usage(uid, link, kind, "failed", str(e))
    except Exception as e:
        logging.exception("download error")
        await status.edit_text("❌ Kuch gadbad hui, thodi der baad try karo.")
        await db.log_usage(uid, link, kind, "failed", str(e))
    finally:
        busy.discard(uid)
        shutil.rmtree(tmp, ignore_errors=True)


async def main():
    await app.start()
    logging.info("Bot started")
    await idle()
    await app.stop()


if __name__ == "__main__":
    loop.run_until_complete(main())
