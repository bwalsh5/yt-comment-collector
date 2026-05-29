# YT Live Monitor

A web app for tracking real-time metrics from a YouTube live stream. Polls the YouTube Data API v3 on a configurable interval and displays concurrent viewers, total views, likes, and comments as they change — along with a time-series chart. Live chat comments are recorded in the background and exported to their own CSV. Both the metrics and comments CSVs share a `timestamp` column, so they merge cleanly and plot on the same time axis.

![Python](https://img.shields.io/badge/python-3.10+-blue) ![Flask](https://img.shields.io/badge/flask-3.0-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What it does

- **Pastes in any YouTube stream URL** — supports `watch?v=`, `youtu.be/`, `/live/`, or a bare 11-character video ID
- **Polls on a schedule** — every 10–120 seconds (your choice), fetches the latest metrics from the API
- **Live metrics dashboard** — concurrent viewers (the headline number), total views, likes, and comment count, each showing the per-poll delta
- **Viewer trend chart** — a rolling line chart of concurrent viewers over time (last 120 data points)
- **Comment recording** — if the stream has an active chat, comments are captured in the background (not shown on screen) and tagged with the poll-cycle timestamp; respects the API's own polling interval
- **Metrics log** — timestamped record of every poll result
- **Stop any time** — click Stop and all data collected up to that point is available to export
- **Two CSV exports** — a metrics CSV (`timestamp, concurrent_viewers, views, likes, comments, live_status`) and a comments CSV (`timestamp, published_at, author, text, is_mod, is_owner`), both sharing the same `timestamp` so they merge on it

---

## Requirements

- Python 3.10+
- A [YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com) with the API enabled in Google Cloud Console

---

## Setup

### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Add your API key

Copy the example env file and fill in your key:

```bash
cp .env.example .env
```

Open `.env` and replace the placeholder:

```
YOUTUBE_API_KEY=AIzaSy...your_key_here
```

You can also skip this step and paste the key directly into the web UI each time you use it.

---

## Running

### Development

```bash
python3 app.py
```

Open [http://localhost:5001](http://localhost:5001).

### Production

Uses Gunicorn with a single Gevent worker. One worker is required because job state is kept in memory — the SSE stream and the background polling thread must share the same process.

```bash
gunicorn -w 1 -k gevent --timeout 600 -b 0.0.0.0:5001 app:app
```

To change the port:

```bash
PORT=8080 gunicorn -w 1 -k gevent --timeout 600 -b 0.0.0.0:${PORT} app:app
```

---

## Usage

1. **Paste the stream URL** into the Target field (any YouTube URL format works, or just the video ID)
2. **Set the poll interval** — 30 seconds is a good default; lower intervals consume quota faster
3. **API key** — leave blank if you set `YOUTUBE_API_KEY` in `.env`, otherwise paste it into the field
4. **Click Start Monitoring** — the dashboard populates after the first API call returns
5. **Click Stop** when you're done — then click **Metrics CSV** and/or **Comments CSV** to download the data collected

### What each field does

| Field | Description |
|---|---|
| **Stream URL / Video ID** | The YouTube stream to monitor. Any URL format or bare 11-char ID. |
| **Poll Interval** | Seconds between each metrics fetch. Range: 10–120s. |
| **API Key** | Your YouTube Data API v3 key. Overrides the env var if both are set. |

### Metrics CSV columns

| Column | Description |
|---|---|
| `timestamp` | UTC time of the poll (ISO 8601) |
| `concurrent_viewers` | Live viewer count at that moment |
| `views` | Total view count |
| `likes` | Total like count |
| `comments` | Total comment count |
| `live_status` | `live`, `upcoming`, or `none` (ended) |

### Comments CSV columns

| Column | Description |
|---|---|
| `timestamp` | Poll-cycle UTC time the comment was collected (ISO 8601) — **matches the metrics CSV**, so the two files merge/plot on this column |
| `published_at` | When the viewer actually posted the comment (ISO 8601, from the API) |
| `author` | Display name of the commenter |
| `text` | Comment text |
| `is_mod` | `True` if the author is a chat moderator |
| `is_owner` | `True` if the author is the channel owner |

> The `timestamp` is the poll cycle in which the app fetched the comment, not the instant it was posted (that's `published_at`). Several comments collected in one poll share a `timestamp` and align with that poll's metrics row.

---

## API quota

The YouTube Data API has a **10,000 unit/day** free quota. This app costs approximately **1 unit per poll** (one `videos.list` call). Live chat polling adds roughly **1 unit per chat fetch**, on the interval the API specifies (typically 5–10 seconds during an active stream).

| Poll interval | Quota used per hour |
|---|---|
| 10s | ~360 units (metrics) + chat |
| 30s | ~120 units (metrics) + chat |
| 60s | ~60 units (metrics) + chat |

For a typical 2-hour stream at 30s intervals, expect **~240–500 units** total depending on chat activity.

---

## Notes

- **Upcoming streams** — if the stream hasn't started yet, the app will keep polling and update automatically when it goes live
- **Ended streams** — if you point it at a stream that has already ended, it takes one snapshot of the final stats and stops
- **Chat** — only appears if the stream has an active live chat; some streams have chat disabled or members-only
- **Concurrent viewers** — this field is only populated by the API while the stream is actively live; it will show 0 for upcoming or ended streams
