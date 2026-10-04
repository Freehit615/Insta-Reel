# Instagram Reels & Image Downloader Bot

A public Telegram bot (Pyrogram) that downloads Instagram **reels, posts, and carousels** from public accounts. Includes per-user limits, an admin broadcast menu, and a 24-hour stats command. Built for Railway + Supabase.

## Features

- Send an Instagram reel/post link, get the video or image(s) back (carousels are sent as an album, up to 10 items)
- Download chain: `yt-dlp` first for reels, `Instaloader` first for posts, each with the other as fallback
- Daily limit and cooldown per user, one active download per user, global concurrency cap
- Admin broadcast: image + caption + link are copied in the exact same format to every user who has started the bot
- Admin `/status`: active users, new users, and downloads in the last 24 hours
- Temp files are deleted after every request

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Telegram handlers, limits, broadcast, admin menu |
| `downloader.py` | Link parsing and Instagram download logic |
| `db.py` | Supabase queries and config (limits, admin IDs) |
| `requirements.txt` | Python dependencies |

## Setup

### 1. Supabase tables

Run once in the Supabase SQL editor:

```sql
create table if not exists users (
  user_id bigint primary key,
  username text,
  first_name text,
  active boolean default true,
  is_banned boolean default false,
  joined_at timestamptz default now(),
  last_active timestamptz default now()
);

create table if not exists usage_logs (
  id bigserial primary key,
  user_id bigint,
  link text,
  type text,
  status text,
  error text,
  created_at timestamptz default now()
);

create index if not exists usage_logs_user_created_idx on usage_logs (user_id, created_at);

notify pgrst, 'reload schema';
```

RLS can stay enabled: the bot uses the service role (secret) key, which bypasses it.

### 2. Environment variables (Railway)

| Variable | Required | Notes |
|----------|----------|-------|
| `API_ID`, `API_HASH` | yes | From my.telegram.org |
| `BOT_TOKEN` | yes | From @BotFather |
| `SUPABASE_URL` | yes | `https://<project-id>.supabase.co` |
| `SUPABASE_KEY` | yes | Service role / secret key. Never commit it |
| `ADMIN_IDS` | yes | Comma-separated Telegram user IDs, e.g. `123,456` |
| `DAILY_LIMIT` | no | Downloads per user per day (default `20`) |
| `COOLDOWN` | no | Seconds between requests (default `5`) |
| `PROXY` | no | HTTP/SOCKS proxy for Instagram requests |
| `IG_COOKIES_FILE` | no | Path to a Netscape `cookies.txt` (use a dummy account) |
| `CAPTION` | no | Caption added to downloaded media |

### 3. Deploy

1. Push this repo to GitHub.
2. Create a Railway project from the repo.
3. Add the variables above.
4. Start command: `python bot.py`

## Commands

**Everyone**
- `/start`: start the bot, then send a reel or post link

**Admins only** (shown in the admin's `/` menu)
- `/broadcast`: send a post to all users (preview, then confirm)
- `/status`: stats for the last 24 hours
- `/cancel`: cancel the current broadcast draft

The **📢 Broadcast** button on the admin's `/start` message does the same as `/broadcast`.

## Notes

- Instagram often blocks datacenter IPs. If downloads fail, set `PROXY` or `IG_COOKIES_FILE`.
- Telegram bots can upload files up to 50 MB through the Bot API; larger files are rejected with a message.
- Private accounts, stories, and highlights are not supported.
- Broadcast runs at about 25 messages per second and uses the Bot API, so it works for every user who has started the bot, even after a restart. Users who block the bot are marked inactive and skipped.
- Downloading content you do not own may violate Instagram's terms or copyright. Use responsibly.
