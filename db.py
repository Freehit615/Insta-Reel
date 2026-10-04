"""
db.py - Supabase data layer + limits config.

Run this SQL once in Supabase SQL editor:

create table users (
  user_id bigint primary key,
  username text,
  first_name text,
  active boolean default true,
  is_banned boolean default false,
  joined_at timestamptz default now(),
  last_active timestamptz default now()
);
create table usage_logs (
  id bigserial primary key,
  user_id bigint,
  link text,
  type text,
  status text,
  error text,
  created_at timestamptz default now()
);
create index on usage_logs (user_id, created_at);
"""
import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"].strip().strip("\"'")
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip().strip("\"'")
if not SUPABASE_URL.startswith("http"):
    SUPABASE_URL = "https://" + SUPABASE_URL
sb = create_client(SUPABASE_URL, SUPABASE_KEY)  # service_role / secret key

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "20"))
COOLDOWN = int(os.getenv("COOLDOWN", "5"))
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}


def _now():
    return datetime.now(timezone.utc).isoformat()


async def add_user(u):
    def f():
        sb.table("users").upsert({
            "user_id": u.id,
            "username": u.username,
            "first_name": u.first_name,
            "active": True,
            "last_active": _now(),
        }).execute()
    try:
        await asyncio.to_thread(f)
    except Exception:
        logging.exception("add_user failed")


async def is_banned(uid):
    def f():
        r = sb.table("users").select("is_banned").eq("user_id", uid).limit(1).execute()
        return bool(r.data and r.data[0]["is_banned"])
    try:
        return await asyncio.to_thread(f)
    except Exception:
        return False


async def count_today(uid):
    def f():
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        r = (sb.table("usage_logs").select("id", count="exact")
             .eq("user_id", uid).eq("status", "success").gte("created_at", start).execute())
        return r.count or 0
    try:
        return await asyncio.to_thread(f)
    except Exception:
        return 0


async def log_usage(uid, link, kind, status, error=None):
    def f():
        sb.table("usage_logs").insert({
            "user_id": uid, "link": link, "type": kind,
            "status": status, "error": (error or "")[:300] or None,
        }).execute()
    try:
        await asyncio.to_thread(f)
    except Exception:
        logging.exception("log_usage failed")


async def active_user_ids():
    def f():
        ids, start = [], 0
        while True:
            r = (sb.table("users").select("user_id").eq("active", True).eq("is_banned", False)
                 .order("user_id").range(start, start + 999).execute())
            ids += [x["user_id"] for x in r.data]
            if len(r.data) < 1000:
                break
            start += 1000
        return ids
    return await asyncio.to_thread(f)


async def stats_24h():
    """Active users (any interaction), new users, downloads ok/failed in last 24h + total users."""
    def f():
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        def cnt(table, col, **flt):
            q = sb.table(table).select(col, count="exact")
            for k, v in flt.get("eq", {}).items():
                q = q.eq(k, v)
            if "gte" in flt:
                q = q.gte(*flt["gte"])
            return q.execute().count or 0

        return {
            "active": cnt("users", "user_id", gte=("last_active", since)),
            "new": cnt("users", "user_id", gte=("joined_at", since)),
            "total": cnt("users", "user_id"),
            "ok": cnt("usage_logs", "id", eq={"status": "success"}, gte=("created_at", since)),
            "bad": cnt("usage_logs", "id", eq={"status": "failed"}, gte=("created_at", since)),
        }
    try:
        return await asyncio.to_thread(f)
    except Exception:
        logging.exception("stats_24h failed")
        return None


async def set_inactive(uid):
    def f():
        sb.table("users").update({"active": False}).eq("user_id", uid).execute()
    try:
        await asyncio.to_thread(f)
    except Exception:
        pass
