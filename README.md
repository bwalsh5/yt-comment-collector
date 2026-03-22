# YT Comment Collector

A web app for collecting and exporting YouTube comments via the YouTube Data API v3. Supports single videos and entire channels, with real-time progress streaming and one-click CSV export.

![Python](https://img.shields.io/badge/python-3.10+-blue) ![Flask](https://img.shields.io/badge/flask-3.0-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Features

- **Single video or full channel** — collect by video ID, @handle, username, or channel name
- **Unlimited collection** — paginate through all available comments, or set a per-video cap
- **Real-time log** — live streaming progress as comments are fetched
- **Preview table** — first 200 rows shown inline after collection completes
- **CSV export** — full dataset downloaded client-side; no server state required

---

## Requirements

- Python 3.10+
- A [YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com)

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/yt-comment-collector.git
cd yt-comment-collector

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
cp .env.example .env
# Edit .env and replace 'your_api_key_here' with your actual key
```

---

## Running

### Development

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

### Production (recommended)

Uses Gunicorn with a single Gevent worker. A single worker is required because job state is kept in memory and must be accessible across the SSE stream and the same request lifecycle.

```bash
gunicorn -w 1 -k gevent --timeout 300 -b 0.0.0.0:5000 app:app
```

To change the port, set the `PORT` environment variable:

```bash
PORT=8080 gunicorn -w 1 -k gevent --timeout 300 -b 0.0.0.0:${PORT} app:app
```

---

## Usage

| Field | Description |
|---|---|
| **Mode** | *Single Video* — one video by ID. *Channel* — most recent N videos from a channel. |
| **Target** | Video ID (e.g. `dQw4w9WgXcQ`), `@handle`, legacy username, or channel name. |
| **Max Comments** | Per-video cap (100–2000). Check *collect all* to remove the cap entirely. |
| **Max Videos** | *(Channel mode only)* Number of most-recent uploads to process (1–50). |
| **API Key** | Entered in the form, or set via `YOUTUBE_API_KEY` in `.env` (recommended). |

After collection, click **Export CSV** to download the full dataset. Columns: `author`, `text`, `date`, `likes`, `video_title`, `video_id`, `channel`.

---

## API Quota

The YouTube Data API has a **10,000 unit/day** free quota. Approximate costs per operation:

| Operation | Cost |
|---|---|
| Fetch 100 comments (1 page) | ~1 unit |
| Channel ID lookup | ~1–5 units |
| Video list (50 videos) | ~1–2 units |

Collecting 500 comments from 10 videos ≈ ~60 units. Collecting all comments from a large channel can consume several thousand units.

---

## Notes

- Videos with comments disabled are silently skipped (logged as a warning).
- Channel name resolution uses a fuzzy search — the log will note if it falls back to a closest match. Prefer `@handle` for accuracy.
- The original standalone CLI script (`yt_comments.py`) also reads `YOUTUBE_API_KEY` from `.env` and can be run independently.
