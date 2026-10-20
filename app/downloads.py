from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
import imageio_ffmpeg
from yt_dlp.utils import DownloadError

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/data/downloads")).resolve()
STATE_DIR = Path(os.getenv("STATE_DIR", "/data/state")).resolve()
DB_PATH = STATE_DIR / "jobs.sqlite3"
MAX_GIB = float(os.getenv("MAX_DOWNLOAD_GIB", "10"))
MAX_JOBS = int(os.getenv("MAX_ACTIVE_JOBS", "20"))
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtube-nocookie.com"}

queue: asyncio.Queue[str] = asyncio.Queue()
cancel_events: dict[str, asyncio.Event] = {}
info_cache: dict[str, tuple[float, dict]] = {}
worker_task: asyncio.Task | None = None


def init_storage() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
            quality TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
            filename TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL
        )""")
        db.execute("UPDATE jobs SET status='failed', error='Interrupted by application restart' WHERE status IN ('queued','downloading')")


def validate_youtube_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise ValueError("Enter a valid YouTube video URL.") from exc
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS or parsed.username or parsed.password or parsed.port:
        raise ValueError("Only standard YouTube video links are supported.")
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        from urllib.parse import parse_qs
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    else:
        match = re.fullmatch(r"/(?:shorts|embed|live)/([\w-]{6,})/?", parsed.path)
        video_id = match.group(1) if match else ""
    if not re.fullmatch(r"[\w-]{6,20}", video_id):
        raise ValueError("That link does not contain a supported YouTube video.")
    return value.strip()


def _ydl_options(*, quiet=True) -> dict:
    opts = {"quiet": quiet, "no_warnings": True, "noplaylist": True, "socket_timeout": 20, "retries": 2}
    cookiefile = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookiefile and from_browser:
        raise RuntimeError("Configure either YTDLP_COOKIES_FILE or YTDLP_COOKIES_FROM_BROWSER, not both.")
    if cookiefile:
        path = Path(cookiefile)
        if not path.is_file():
            raise RuntimeError("Configured cookie file is missing or is not a regular file.")
        if not os.access(path, os.R_OK):
            raise RuntimeError("Configured cookie file is unreadable by the application.")
        try:
            with path.open("r", encoding="utf-8", errors="strict") as file:
                head = file.read(4096)
            if "# Netscape HTTP Cookie File" not in head and "# HTTP Cookie File" not in head:
                raise RuntimeError("Configured cookie file is invalid: it is not in Netscape cookie format.")
        except UnicodeError as exc:
            raise RuntimeError("Configured cookie file is unreadable text.") from exc
        opts["cookiefile"] = str(path)
    elif from_browser:
        opts["cookiesfrombrowser"] = (from_browser.split(":", 1)[0], from_browser.split(":", 1)[1] if ":" in from_browser else "default")
    return opts


def _error_message(error: Exception) -> str:
    raw = str(error)
    low = raw.lower()
    if "requested format is not available" in low or "requested format not available" in low:
        return "YouTube did not offer that quality as a single video file. Try Best available or a lower quality."
    if "cookie file" in low and ("missing" in low or "not a regular" in low):
        return "The configured YouTube cookie file is missing. Check YTDLP_COOKIES_FILE and its mounted path."
    if "cookie file" in low and "unreadable" in low:
        return "The configured YouTube cookie file is unreadable. Check its mount and file permissions."
    if "cookie file" in low and "invalid" in low:
        return "The configured cookie file is invalid. Export it in Netscape HTTP Cookie File format."
    if "cookies" in low and ("expired" in low or "no longer valid" in low):
        return "Your YouTube cookies have expired. Sign in again and export a fresh Netscape-format cookie file."
    if "cookie" in low and ("decrypt" in low or "permission" in low or "database" in low or "locked" in low):
        return "YouTube cookies could not be read. Export a fresh Netscape-format cookie file and configure YTDLP_COOKIES_FILE."
    if "sign in" in low or "confirm you are not a bot" in low:
        return "YouTube requires sign-in or additional verification. Sign in using the browser, then export fresh cookies."
    if "private video" in low:
        return "This video is private or unavailable to the configured account. Check your sign-in and permissions."
    if "not available in your country" in low or "geo restricted" in low or "geographic restriction" in low:
        return "This video is restricted in your region."
    if "not available" in low or "unavailable" in low:
        return "YouTube did not provide a downloadable video. It may require sign-in or be restricted in your region."
    if "no such file" in low or "permission denied" in low:
        return "The cookie file or download storage could not be accessed. Check the configured paths and permissions."
    return "YouTube could not complete this request. Check the URL, cookies, network connection, and yt-dlp logs."


def fetch_info(url: str) -> dict:
    clean_url = validate_youtube_url(url)
    key = clean_url
    cached = info_cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
            raw = ydl.extract_info(clean_url, download=False)
    except (DownloadError, RuntimeError, OSError) as exc:
        raise ValueError(_error_message(exc)) from None
    if raw.get("_type") == "playlist":
        raise ValueError("Playlist links are not supported. Open a single video instead.")
    formats = []
    seen = set()
    for f in raw.get("formats", []):
        height = f.get("height")
        ext = f.get("ext")
        if height and ext and f.get("vcodec") != "none":
            label = f"{height}p"
            if label not in seen:
                formats.append({"label": label, "height": height, "ext": ext, "hasAudio": f.get("acodec") != "none"})
                seen.add(label)
    result = {
        "id": raw.get("id", ""), "title": raw.get("title") or "YouTube video", "thumbnail": raw.get("thumbnail"),
        "duration": raw.get("duration"), "uploader": raw.get("uploader"),
        "formats": sorted(formats, key=lambda x: x["height"], reverse=True),
    }
    info_cache[key] = (time.time() + 900, result)
    return result


def _job_row(job_id: str):
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def list_jobs() -> list[dict]:
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100").fetchall()
    return [dict(row) for row in rows]


async def create_job(url: str, quality: str) -> dict:
    if quality not in {"best", "2160", "1440", "1080", "720", "480", "360"}:
        raise ValueError("Choose one of the available video qualities.")
    active = [j for j in list_jobs() if j["status"] in {"queued", "downloading"}]
    if len(active) >= MAX_JOBS:
        raise ValueError("The download queue is full. Wait for an active download to finish.")
    info = await asyncio.to_thread(fetch_info, url)
    jid, now = uuid.uuid4().hex, time.time()
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO jobs (id,url,title,kind,quality,status,created,updated) VALUES (?,?,?,?,?,'queued',?,?)",
                   (jid, url.strip(), info["title"][:300], "video", quality, now, now))
    cancel_events[jid] = asyncio.Event()
    await queue.put(jid)
    return dict(_job_row(jid))


def _format_selector(quality: str) -> str:
    if quality == "best":
        return "bestvideo+bestaudio/best"
    return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"


async def _run_download(job: dict, cancel_event: asyncio.Event) -> None:
    jid = job["id"]
    outtmpl = str(DOWNLOAD_DIR / f"{jid}-%(title).140B.%(ext)s")
    cmd = [os.sys.executable, "-m", "yt_dlp", "--newline", "--no-playlist", "--no-warnings", "--progress-template", "download:__PERCENT__%(progress._percent_str)s", "--max-filesize", f"{MAX_GIB}G", "--ffmpeg-location", imageio_ffmpeg.get_ffmpeg_exe(), "-f", _format_selector(job["quality"]), "-o", outtmpl, "--merge-output-format", "mp4", "--restrict-filenames"]
    cookiefile = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    try:
        opts = _ydl_options()
    except Exception as exc:
        raise ValueError(_error_message(exc)) from None
    if cookiefile:
        cmd += ["--cookies", cookiefile]
    if from_browser:
        cmd += ["--cookies-from-browser", from_browser]
    cmd.append(job["url"])
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("UPDATE jobs SET status='downloading', updated=? WHERE id=?", (time.time(), jid))
    output_tail = []
    try:
        while True:
            if cancel_event.is_set() and proc.returncode is None:
                proc.terminate()
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if "__PERCENT__" in text:
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)%", text)
                if match:
                    with sqlite3.connect(DB_PATH) as db:
                        db.execute("UPDATE jobs SET progress=?, updated=? WHERE id=?", (float(match.group(1)), time.time(), jid))
            elif text:
                output_tail.append(text[:400])
                output_tail = output_tail[-12:]
        code = await proc.wait()
        if cancel_event.is_set():
            for partial in DOWNLOAD_DIR.glob(f"{jid}-*"):
                if partial.is_file():
                    partial.unlink(missing_ok=True)
            with sqlite3.connect(DB_PATH) as db:
                db.execute("UPDATE jobs SET status='cancelled', error=NULL, updated=? WHERE id=?", (time.time(), jid))
            return
        if code:
            raise RuntimeError(" ".join(output_tail))
        candidates = list(DOWNLOAD_DIR.glob(f"{jid}-*"))
        candidates = [p for p in candidates if p.is_file() and p.suffix not in {".part", ".ytdl"}]
        if not candidates:
            raise RuntimeError("No completed download file was produced.")
        completed = max(candidates, key=lambda p: p.stat().st_mtime)
        if completed.stat().st_size > MAX_GIB * 1024**3:
            completed.unlink(missing_ok=True)
            raise RuntimeError("This file exceeds the configured download size limit.")
        with sqlite3.connect(DB_PATH) as db:
            db.execute("UPDATE jobs SET status='completed', progress=100, filename=?, updated=? WHERE id=?", (completed.name, time.time(), jid))
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _worker() -> None:
    while True:
        jid = await queue.get()
        try:
            row = _job_row(jid)
            if not row:
                continue
            event = cancel_events.setdefault(jid, asyncio.Event())
            if event.is_set():
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE jobs SET status='cancelled', updated=? WHERE id=?", (time.time(), jid))
                continue
            try:
                await _run_download(dict(row), event)
            except Exception as exc:
                for partial in DOWNLOAD_DIR.glob(f"{jid}-*"):
                    if partial.is_file():
                        partial.unlink(missing_ok=True)
                with sqlite3.connect(DB_PATH) as db:
                    db.execute("UPDATE jobs SET status='failed', error=?, updated=? WHERE id=?", (_error_message(exc), time.time(), jid))
        finally:
            queue.task_done()


def start_worker() -> None:
    global worker_task
    if worker_task is None or worker_task.done():
        worker_task = asyncio.create_task(_worker())


async def cancel_job(job_id: str) -> bool:
    row = _job_row(job_id)
    if not row:
        return False
    if row["status"] in {"queued", "downloading"}:
        cancel_events.setdefault(job_id, asyncio.Event()).set()
    return True


def get_job(job_id: str) -> dict | None:
    row = _job_row(job_id)
    return dict(row) if row else None


def safe_download_path(job: dict) -> Path:
    filename = job.get("filename")
    if not filename or Path(filename).name != filename:
        raise FileNotFoundError
    path = (DOWNLOAD_DIR / filename).resolve()
    if path.parent != DOWNLOAD_DIR or not path.is_file():
        raise FileNotFoundError
    return path


def delete_job_file(job_id: str) -> bool:
    job = get_job(job_id)
    if not job:
        return False
    if job["status"] in {"queued", "downloading"}:
        return False
    if job.get("filename"):
        try:
            safe_download_path(job).unlink()
        except FileNotFoundError:
            pass
    with sqlite3.connect(DB_PATH) as db:
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    cancel_events.pop(job_id, None)
    return True
