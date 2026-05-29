"""
YouTube Live Monitor — Flask Web App
--------------------------------------
Polls live stream metrics in real-time via YouTube Data API v3.
API key priority: form input > YOUTUBE_API_KEY environment variable
"""

import os
import re
import json
import time
import uuid
import threading
from datetime import datetime, timezone
from queue import Queue, Empty

from flask import Flask, render_template, request, Response, jsonify
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# In-memory job store: job_id -> {queue, status, data, stop_event}
_jobs: dict = {}
_jobs_lock = threading.Lock()


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def extract_video_id(url_or_id: str) -> str | None:
    """Extract an 11-char video ID from a YouTube URL, or return it directly."""
    s = url_or_id.strip()
    for pat in [
        r'[?&]v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'/live/([a-zA-Z0-9_-]{11})',
        r'/embed/([a-zA-Z0-9_-]{11})',
        r'/shorts/([a-zA-Z0-9_-]{11})',
    ]:
        m = re.search(pat, s)
        if m:
            return m.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', s):
        return s
    return None


# ---------------------------------------------------------------------------
# Live monitor
# ---------------------------------------------------------------------------

class YouTubeLiveMonitor:
    def __init__(self, api_key: str, emit_fn):
        self.youtube = build("youtube", "v3", developerKey=api_key)
        self._emit = emit_fn

    def get_snapshot(self, video_id: str) -> dict | None:
        try:
            resp = self.youtube.videos().list(
                part="snippet,statistics,liveStreamingDetails",
                id=video_id,
            ).execute()
        except HttpError as e:
            self._emit("error", f"API error: {e}")
            return None

        items = resp.get("items")
        if not items:
            return None

        item    = items[0]
        snippet = item.get("snippet", {})
        stats   = item.get("statistics", {})
        live    = item.get("liveStreamingDetails", {})

        return {
            "video_id":           video_id,
            "title":              snippet.get("title", ""),
            "channel":            snippet.get("channelTitle", ""),
            "live_status":        snippet.get("liveBroadcastContent", "none"),
            "concurrent_viewers": int(live.get("concurrentViewers") or 0),
            "views":              int(stats.get("viewCount") or 0),
            "likes":              int(stats.get("likeCount") or 0),
            "comments":           int(stats.get("commentCount") or 0),
            "scheduled_start":    live.get("scheduledStartTime"),
            "actual_start":       live.get("actualStartTime"),
            "live_chat_id":       live.get("activeLiveChatId"),
        }

    def get_chat_page(self, chat_id: str, page_token: str | None) -> dict:
        try:
            kwargs: dict = {
                "part":       "snippet,authorDetails",
                "liveChatId": chat_id,
                "maxResults": 200,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            return self.youtube.liveChatMessages().list(**kwargs).execute()
        except HttpError as e:
            self._emit("warn", f"Chat unavailable: {e}")
            return {}

    def monitor(self, video_id: str, interval: int, stop_event: threading.Event) -> list:
        snapshots: list = []

        # Initial probe
        snap = self.get_snapshot(video_id)
        if snap is None:
            self._emit("error", f"Video not found: {video_id}")
            return []

        self._emit("info", f"Title:   {snap['title']}")
        self._emit("info", f"Channel: {snap['channel']}")
        self._emit("info", f"Status:  {snap['live_status']}")

        if snap["live_status"] == "upcoming":
            start = snap.get("scheduled_start") or "unknown"
            self._emit("warn", f"Stream not yet live. Scheduled: {start}. Polling until live…")
        elif snap["live_status"] == "none":
            self._emit("warn", "Video is not live. Recording current stats.")

        # Send video metadata to the frontend
        self._emit("videoinfo", json.dumps({
            "title":    snap["title"],
            "channel":  snap["channel"],
            "video_id": video_id,
            "status":   snap["live_status"],
        }))

        chat_page_token: str | None = None
        chat_next_poll: float = 0.0
        tick = 0

        while not stop_event.is_set():
            now = time.time()

            snap = self.get_snapshot(video_id)
            if snap is None:
                self._emit("error", "Lost contact with API.")
                break

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            row = {
                "timestamp":          ts,
                "concurrent_viewers": snap["concurrent_viewers"],
                "views":              snap["views"],
                "likes":              snap["likes"],
                "comments":           snap["comments"],
                "live_status":        snap["live_status"],
            }
            snapshots.append(row)
            self._emit("metric", json.dumps(row))
            self._emit("log", (
                f"[{ts}] "
                f"viewers={snap['concurrent_viewers']:,}  "
                f"views={snap['views']:,}  "
                f"likes={snap['likes']:,}  "
                f"comments={snap['comments']:,}"
            ))

            # Live chat polling (respects API-provided interval)
            chat_id = snap.get("live_chat_id")
            if chat_id and now >= chat_next_poll:
                chat_resp = self.get_chat_page(chat_id, chat_page_token)
                msgs = []
                for item in chat_resp.get("items", []):
                    s = item.get("snippet", {})
                    a = item.get("authorDetails", {})
                    if s.get("type") == "textMessageEvent":
                        msgs.append({
                            # Poll-cycle timestamp — identical to the metrics row
                            # collected this loop, so the two CSVs merge on it.
                            "timestamp":    ts,
                            "published_at": s.get("publishedAt", ""),
                            "author":       a.get("displayName", ""),
                            "text":         s.get("displayMessage", ""),
                            "is_mod":       a.get("isChatModerator", False),
                            "is_owner":     a.get("isChatOwner", False),
                        })
                if msgs:
                    self._emit("chat", json.dumps(msgs))
                chat_page_token = chat_resp.get("nextPageToken")
                poll_ms = chat_resp.get("pollingIntervalMillis", 5000)
                chat_next_poll = now + max(poll_ms / 1000, 2.0)

            # Stop when stream ends (but always take at least one snapshot)
            if snap["live_status"] == "none" and tick > 0:
                self._emit("info", "Stream has ended.")
                break

            # Sleep until next metrics poll, honouring stop_event
            deadline = now + interval
            while time.time() < deadline and not stop_event.is_set():
                time.sleep(0.5)

            tick += 1

        return snapshots


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _run_job(job_id: str, video_id: str, api_key: str, interval: int):
    with _jobs_lock:
        q:    Queue           = _jobs[job_id]["queue"]
        stop: threading.Event = _jobs[job_id]["stop_event"]

    def emit(type_: str, msg: str):
        q.put(json.dumps({"type": type_, "message": msg}))

    try:
        monitor = YouTubeLiveMonitor(api_key, emit)
        rows    = monitor.monitor(video_id, interval, stop)
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["data"]   = rows
        emit("done", json.dumps({"total": len(rows), "rows": rows}))
    except Exception as e:
        emit("error", str(e))
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
    finally:
        q.put(None)  # sentinel — tells SSE generator to stop


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/monitor", methods=["POST"])
def start_monitor():
    data    = request.get_json(force=True)
    api_key = (data.get("api_key") or "").strip() or os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return jsonify(error="YouTube API key required."), 400

    url      = (data.get("url") or "").strip()
    video_id = extract_video_id(url)
    if not video_id:
        return jsonify(error=f"Cannot parse a video ID from: {url!r}"), 400

    interval = max(10, min(int(data.get("interval", 30)), 300))

    job_id     = uuid.uuid4().hex[:12]
    stop_event = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = {
            "queue":      Queue(),
            "status":     "running",
            "data":       None,
            "stop_event": stop_event,
        }

    threading.Thread(
        target=_run_job,
        args=(job_id, video_id, api_key, interval),
        daemon=True,
    ).start()

    return jsonify(job_id=job_id, video_id=video_id)


@app.route("/stop/<job_id>", methods=["POST"])
def stop_monitor(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify(error="Job not found."), 404
    job["stop_event"].set()
    return jsonify(ok=True)


@app.route("/stream/<job_id>")
def stream(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        err = json.dumps({"type": "error", "message": "Job not found"})
        return Response(f"data: {err}\n\n", mimetype="text/event-stream")

    q: Queue = job["queue"]

    def generate():
        while True:
            try:
                item = q.get(timeout=30)
            except Empty:
                yield 'data: {"type":"ping"}\n\n'
                continue
            if item is None:
                break
            yield f"data: {item}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, threaded=True)
