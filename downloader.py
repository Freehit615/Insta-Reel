"""
downloader.py - Instagram reel/image downloader (sync, call via asyncio.to_thread).
Reels: yt-dlp first, Instaloader fallback. Posts/images: Instaloader first, yt-dlp fallback.
"""
import os
import re
import glob
import requests
import yt_dlp
import instaloader

PROXY = os.getenv("PROXY") or None
COOKIES = os.getenv("IG_COOKIES_FILE")  # optional: Netscape cookies.txt of a dummy account
MAX_MB = 50
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")

URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(p|reel|reels|tv)/([\w-]+)", re.I
)


class DlError(Exception):
    pass


def parse(text):
    """Returns (kind, shortcode, canonical_url) or None."""
    m = URL_RE.search(text or "")
    if not m:
        return None
    kind, code = m.group(1).lower(), m.group(2)
    kind = "reel" if kind in ("reel", "reels", "tv") else "p"
    return kind, code, f"https://www.instagram.com/{kind}/{code}/"


def _ytdlp(url, out):
    opts = {
        "outtmpl": f"{out}/%(id)s_%(autonumber)s.%(ext)s",
        "format": "mp4/best",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }
    if PROXY:
        opts["proxy"] = PROXY
    if COOKIES and os.path.exists(COOKIES):
        opts["cookiefile"] = COOKIES
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([url])
    files = []
    for f in sorted(glob.glob(f"{out}/*")):
        if f.lower().endswith(VIDEO_EXT):
            files.append((f, "video"))
        elif f.lower().endswith(IMAGE_EXT):
            files.append((f, "photo"))
    return files


def _insta(code, out):
    L = instaloader.Instaloader(
        download_pictures=False, download_videos=False,
        save_metadata=False, quiet=True,
    )
    if PROXY:
        L.context._session.proxies = {"http": PROXY, "https": PROXY}
    post = instaloader.Post.from_shortcode(L.context, code)

    items = []
    if post.typename == "GraphSidecar":
        for n in post.get_sidecar_nodes():
            items.append((n.video_url, "video") if n.is_video else (n.display_url, "photo"))
    elif post.is_video:
        items.append((post.video_url, "video"))
    else:
        items.append((post.url, "photo"))

    files = []
    for i, (u, kind) in enumerate(items[:10]):
        path = f"{out}/{code}_{i}.{'mp4' if kind == 'video' else 'jpg'}"
        r = requests.get(u, timeout=30, stream=True, proxies={"http": PROXY, "https": PROXY} if PROXY else None)
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        files.append((path, kind))
    return files


def download(text, out):
    """Returns list of (path, 'video'|'photo'). Raises DlError with user-friendly message."""
    p = parse(text)
    if not p:
        raise DlError("Valid Instagram reel/post link bhejo.")
    kind, code, url = p

    order = [lambda: _ytdlp(url, out), lambda: _insta(code, out)]
    if kind == "p":
        order.reverse()

    files, err = [], None
    for fn in order:
        try:
            files = fn()
            if files:
                break
        except Exception as e:
            err = e

    if not files:
        msg = str(err).lower() if err else ""
        if "private" in msg or "login" in msg or "not available" in msg:
            raise DlError("Account private h ya content available nahi.")
        raise DlError("Download fail hua. Link check karo ya thodi der baad try karo.")

    for f, _ in files:
        if os.path.getsize(f) > MAX_MB * 1024 * 1024:
            raise DlError("File 50 MB se badi h, Telegram pe nahi bhej sakta.")
    return files
