"""
YouTube Comment Collector — Flask Web App
------------------------------------------
API key priority: form input > YOUTUBE_API_KEY environment variable
"""

import os
import uuid
import threading
import json
import io
import queue
from datetime import datetime

from flask import Flask, render_template, request, Response, jsonify
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# In-memory job store: job_id -> {'queue': Queue, 'data': DataFrame|None, 'status': str}
_jobs: dict = {}
_jobs_lock = threading.Lock()


class YouTubeCommentCollector:
    """YouTube comment collector with real-time progress callbacks."""

    def __init__(self, api_key: str, emit_fn=None):
        self.youtube = build("youtube", "v3", developerKey=api_key)
        self._emit = emit_fn or (lambda t, m: None)

    def _log(self, message: str, type: str = "log"):
        self._emit(type, message)

    def get_video_info(self, video_id: str) -> dict:
        try:
            resp = self.youtube.videos().list(part="snippet", id=video_id).execute()
            if resp.get("items"):
                s = resp["items"][0]["snippet"]
                return {
                    "video_id": video_id,
                    "title": s["title"],
                    "channel": s["channelTitle"],
                }
        except HttpError as e:
            self._log(f"Error fetching video info: {e}", "error")
        return {"video_id": video_id, "title": "Unknown", "channel": "Unknown"}

    def resolve_channel_id(self, channel_input: str) -> str | None:
        """Resolve a channel ID from a channel ID, username, @handle, or search query."""
        inp = channel_input.strip()

        # Already a channel ID
        if inp.startswith("UC") and len(inp) == 24:
            return inp

        try:
            # Try legacy username lookup first (no @ prefix)
            if not inp.startswith("@"):
                resp = (
                    self.youtube.channels()
                    .list(part="id", forUsername=inp)
                    .execute()
                )
                if resp.get("items"):
                    return resp["items"][0]["id"]

            # Search by handle or name
            query = inp.lstrip("@")
            resp = (
                self.youtube.search()
                .list(part="snippet", q=query, type="channel", maxResults=5)
                .execute()
            )
            query_lower = query.lower()
            for item in resp.get("items", []):
                title = item["snippet"]["channelTitle"].lower()
                if query_lower in title or title in query_lower:
                    return item["snippet"]["channelId"]

            # Fall back to first result
            if resp.get("items"):
                first = resp["items"][0]
                self._log(
                    f"Using closest match: {first['snippet']['channelTitle']}", "warn"
                )
                return first["snippet"]["channelId"]

        except HttpError as e:
            self._log(f"HTTP error resolving channel: {e}", "error")

        return None

    def get_channel_videos(self, channel_id: str, max_results: int = 50) -> list:
        videos = []
        try:
            resp = (
                self.youtube.channels()
                .list(part="contentDetails", id=channel_id)
                .execute()
            )
            if not resp.get("items"):
                return videos

            playlist_id = resp["items"][0]["contentDetails"]["relatedPlaylists"][
                "uploads"
            ]
            next_page_token = None

            while len(videos) < max_results:
                resp = (
                    self.youtube.playlistItems()
                    .list(
                        part="snippet",
                        playlistId=playlist_id,
                        maxResults=50,
                        pageToken=next_page_token,
                    )
                    .execute()
                )
                for item in resp["items"]:
                    videos.append(
                        {
                            "video_id": item["snippet"]["resourceId"]["videoId"],
                            "title": item["snippet"]["title"],
                        }
                    )
                next_page_token = resp.get("nextPageToken")
                if not next_page_token:
                    break

        except HttpError as e:
            self._log(f"HTTP error fetching videos: {e}", "error")

        return videos[:max_results]

    def get_comments(self, video_id: str, max_comments: int = 0) -> list:
        """Fetch comments for a video. max_comments=0 means collect all available."""
        comments = []
        unlimited = max_comments == 0
        try:
            next_page_token = None
            while True:
                fetch = 100 if unlimited else min(100, max_comments - len(comments))
                resp = (
                    self.youtube.commentThreads()
                    .list(
                        part="snippet",
                        videoId=video_id,
                        maxResults=fetch,
                        pageToken=next_page_token,
                        textFormat="plainText",
                    )
                    .execute()
                )
                for item in resp["items"]:
                    c = item["snippet"]["topLevelComment"]["snippet"]
                    comments.append(
                        {
                            "text": c["textDisplay"],
                            "author": c["authorDisplayName"],
                            "date": c["publishedAt"],
                            "likes": c["likeCount"],
                        }
                    )
                next_page_token = resp.get("nextPageToken")
                if not next_page_token:
                    break
                if not unlimited and len(comments) >= max_comments:
                    break

        except HttpError as e:
            if e.resp.status == 403:
                self._log(f"Comments disabled for video {video_id}", "warn")
            else:
                self._log(f"HTTP error fetching comments: {e}", "error")

        return comments if unlimited else comments[:max_comments]

    def collect_video(self, video_id: str, max_comments: int = 0) -> list:
        self._log("Fetching video info...")
        info = self.get_video_info(video_id)
        self._log(f"Video: {info['title']}", "success")
        label = "all available" if max_comments == 0 else str(max_comments)
        self._log(f"Collecting {label} comments...")
        comments = self.get_comments(video_id, max_comments)
        for c in comments:
            c.update(
                {
                    "video_id": video_id,
                    "video_title": info["title"],
                    "channel": info["channel"],
                }
            )
        self._log(f"Collected {len(comments)} comments", "success")
        return comments

    def collect_channel(
        self,
        channel_input: str,
        max_videos: int = 10,
        max_comments_per_video: int = 0,
    ) -> list:
        self._log(f"Resolving channel: {channel_input}")
        channel_id = self.resolve_channel_id(channel_input)

        if not channel_id:
            self._log(f"Could not find channel: {channel_input}", "error")
            return []

        self._log(f"Channel ID: {channel_id}")
        self._log(f"Fetching up to {max_videos} videos...")
        videos = self.get_channel_videos(channel_id, max_results=max_videos)
        self._log(f"Found {len(videos)} videos", "success")

        all_comments = []
        for i, video in enumerate(videos, 1):
            short_title = (
                video["title"][:60] + "…"
                if len(video["title"]) > 60
                else video["title"]
            )
            self._log(f"[{i}/{len(videos)}] {short_title}")
            comments = self.get_comments(
                video["video_id"], max_comments=max_comments_per_video
            )
            for c in comments:
                c.update(
                    {"video_id": video["video_id"], "video_title": video["title"]}
                )
            all_comments.extend(comments)
            self._log(
                f"  ↳ {len(comments)} comments | {len(all_comments)} total",
                "progress",
            )

        self._log(
            f"Complete — {len(all_comments)} comments collected", "success"
        )
        return all_comments


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _run_job(
    job_id: str,
    mode: str,
    target: str,
    api_key: str,
    max_comments: int,
    max_videos: int,
):
    q = _jobs[job_id]["queue"]

    def emit(type_: str, message: str):
        q.put(json.dumps({"type": type_, "message": message}))

    try:
        collector = YouTubeCommentCollector(api_key, emit_fn=emit)

        if mode == "video":
            comments = collector.collect_video(target, max_comments=max_comments)
        else:
            comments = collector.collect_channel(
                target,
                max_videos=max_videos,
                max_comments_per_video=max_comments,
            )

        df = pd.DataFrame(comments) if comments else pd.DataFrame()
        if not df.empty and "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d %H:%M")

        with _jobs_lock:
            _jobs[job_id]["data"] = df
            _jobs[job_id]["status"] = "done"

        # Send all rows to the client so CSV can be generated client-side
        rows = []
        if not df.empty:
            for _, row in df.iterrows():
                rows.append(
                    {
                        "author":      str(row.get("author", "")),
                        "text":        str(row.get("text", "")),
                        "date":        str(row.get("date", ""))[:16],
                        "likes":       int(row.get("likes", 0)),
                        "video_title": str(row.get("video_title", "")),
                        "video_id":    str(row.get("video_id", "")),
                        "channel":     str(row.get("channel", "")),
                    }
                )

        q.put(json.dumps({"type": "done", "total": len(df), "rows": rows}))

    except Exception as e:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
        q.put(json.dumps({"type": "error", "message": str(e)}))
    finally:
        q.put(None)  # sentinel — tells the SSE generator to stop


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/collect", methods=["POST"])
def collect():
    data = request.get_json()
    api_key = (data.get("api_key") or "").strip() or os.environ.get(
        "YOUTUBE_API_KEY", ""
    )

    if not api_key:
        return (
            jsonify(
                {
                    "error": "YouTube API key is required. "
                    "Set the YOUTUBE_API_KEY environment variable or enter it in the form."
                }
            ),
            400,
        )

    mode = data.get("mode", "video")
    target = (data.get("target") or "").strip()
    if not target:
        return jsonify({"error": "Target (video ID or channel) is required."}), 400

    try:
        max_comments_raw = int(data.get("max_comments", 0))
        # 0 = unlimited; otherwise clamp to a reasonable upper bound
        max_comments = 0 if max_comments_raw == 0 else max(1, min(max_comments_raw, 10_000))
        max_videos = max(1, min(int(data.get("max_videos", 10)), 50))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid parameter values."}), 400

    job_id = uuid.uuid4().hex[:10]
    with _jobs_lock:
        _jobs[job_id] = {
            "queue": queue.Queue(),
            "data": None,
            "status": "running",
        }

    threading.Thread(
        target=_run_job,
        args=(job_id, mode, target, api_key, max_comments, max_videos),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id: str):
    if job_id not in _jobs:
        return jsonify({"error": "Job not found"}), 404

    def generate():
        q = _jobs[job_id]["queue"]
        while True:
            item = q.get()
            if item is None:
                break
            yield f"data: {item}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/download/<job_id>")
def download(job_id: str):
    if job_id not in _jobs:
        return jsonify({"error": "Job not found"}), 404

    with _jobs_lock:
        df = _jobs[job_id].get("data")

    if df is None or df.empty:
        return jsonify({"error": "No data available for this job."}), 404

    buf = io.StringIO()
    df.to_csv(buf, index=False)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=yt_comments_{timestamp}.csv"
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
