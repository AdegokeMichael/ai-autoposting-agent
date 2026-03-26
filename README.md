# AI Autoposting Agent 🎬

**Record once. AI clips, writes, schedules, and floods your TikTok — automatically.**

---

## How it works

```
You record → Drop video → faster-whisper transcribes (free, local)
  → Claude finds viral moments + scores hooks
  → FFmpeg cuts to 9:16 TikTok format
  → Claude writes natural captions + hashtags in your voice
  → Dashboard: you approve / edit / reject
  → TikTok API posts at your scheduled times
  → Hook Learner feeds real stats back into Claude → gets smarter over time
```

---

## Quick Start

### 1. Requirements
- Python 3.10+
- FFmpeg: `brew install ffmpeg` (Mac) or `apt install ffmpeg` (Linux)
- Anthropic API key
- TikTok Business Account with Content Posting API (see below)

### 2. Install
```bash
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Open .env and add your ANTHROPIC_API_KEY at minimum
```

### 4. Verify everything is set up
```bash
python setup_check.py
```

### 5. Run
```bash
python run.py
```

Open **http://localhost:8000** in your browser.

---

## Usage

### Option A — Web Dashboard (recommended)
1. Open http://localhost:8000
2. Drag & drop your video onto the upload zone
3. Wait 2–5 mins for the AI to process
4. Review each clip — edit captions if you want
5. Click **Approve** or **Approve & Post Now**

### Option B — CLI (power user / scripts)
```bash
# Process a video from the terminal
python cli.py run /path/to/myvideo.mp4

# See all pending clips
python cli.py clips --status pending

# Approve a clip
python cli.py approve abc1-clip1

# Post it
python cli.py post abc1-clip1

# Approve ALL pending clips at once
python cli.py approve-all

# Post ALL approved clips to TikTok
python cli.py post-approved

# Record real TikTok stats (feeds into Claude's hook learning)
python cli.py perf abc1-clip1 --views 45000 --watch-rate 0.72 --likes 1200

# View analytics summary
python cli.py analytics
```

### Option C — Drop Folder (hands-off)
Set `WATCH_FOLDER=./watch_inbox` in your `.env`, then run:
```bash
python cli.py watch
```
Drop any video into `watch_inbox/` — the pipeline triggers automatically with zero clicks.

---

## Scheduler

Enable auto-posting in the **Settings** tab in the dashboard:
- Toggle on / off
- Set daily post limit (e.g. 3 per day)
- Set posting times (e.g. 08:00, 13:00, 19:00)
- Set your timezone (default: Africa/Lagos)

Approved clips queue up and post automatically at those times.

---

## Hook Learner — Claude gets smarter over time

After your clips get posted, record their real TikTok performance:

```bash
python cli.py perf abc1-clip1 --views 50000 --watch-rate 0.68 --likes 2300
```

Or use the **Analytics** tab in the dashboard.

Once you have **3+ clips recorded**, Claude starts including "lessons learned" in every new prompt, automatically adjusting which hooks it prioritises based on what's actually working for YOUR audience. At 10+ clips, pattern recognition is strong. At 30+, your audience profile is solid.

---

## TikTok API Setup

1. Go to https://developers.tiktok.com
2. Create an app
3. Request **Content Posting API** scope
4. Complete business verification (takes 2-3 business days from TikTok)
5. Get your access token via OAuth
6. Add to `.env`:
   ```
   TIKTOK_ACCESS_TOKEN=your_token_here
   TIKTOK_CLIENT_KEY=your_client_key
   TIKTOK_CLIENT_SECRET=your_client_secret
   ```

While waiting for TikTok approval, everything else works — you can fully test the pipeline, review clips, and edit captions.

---

## Customising Claude's voice & topics

Edit `app/analyzer.py` — find `BRAND_CONTEXT` and update it with:
- The topics you cover
- Your tone / speaking style
- Words / phrases you use often
- Audience description

The more specific this is, the more captions sound exactly like you wrote them.

---

## Whisper model sizes

Set `WHISPER_MODEL` in `.env`:

| Model     | Speed    | Quality   | RAM needed |
|-----------|----------|-----------|------------|
| tiny      | Fastest  | OK        | ~1 GB      |
| base      | Fast     | Good      | ~1.5 GB    |
| small     | Medium   | Great     | ~2.5 GB    |
| medium    | Slow     | Excellent | ~5 GB      |
| large-v3  | Slowest  | Best      | ~10 GB     |

`base` is the recommended default for most machines.

---

## File Structure

```
ai-autoposting-gent/
├── app/
│   ├── main.py          ← FastAPI routes + app lifecycle
│   ├── pipeline.py      ← Orchestrates the full flow
│   ├── transcriber.py   ← faster-whisper transcription
│   ├── analyzer.py      ← Claude: finds clips, writes captions
│   ├── editor.py        ← FFmpeg: cuts video, generates thumbnails
│   ├── tiktok.py        ← TikTok Content Posting API
│   ├── scheduler.py     ← APScheduler: auto-posts at timed slots
│   ├── watcher.py       ← Watchdog: monitors inbox folder
│   ├── hook_learner.py  ← Tracks performance, feeds lessons to Claude
│   ├── storage.py       ← JSON-based local data store
│   └── models.py        ← Pydantic data models
├── static/
│   └── index.html       ← Dashboard (Clips / Schedule / Analytics / Settings)
├── cli.py               ← Terminal CLI
├── run.py               ← App entry point
├── setup_check.py       ← Pre-flight verification
├── uploads/             ← Uploaded source videos
├── watch_inbox/         ← Drop folder (if WATCH_FOLDER is set)
├── output/
│   ├── clips/           ← Cut video files
│   ├── thumbnails/      ← Clip thumbnails
│   └── store/           ← clips.json, jobs.json, schedule.json, etc.
├── .env                 ← Your secrets (never commit this)
├── .env.example         ← Template
└── requirements.txt
```

---

## Cost breakdown

| Component       | Cost       | Notes                        |
|-----------------|------------|------------------------------|
| faster-whisper  | Free       | Runs locally on your machine |
| Claude API      | ~$0.01–0.05/video | Depends on video length |
| TikTok API      | Free       | Just needs business account  |
| FFmpeg          | Free       | Open source                  |

A 10-minute video typically costs under $0.05 in Claude API calls.
