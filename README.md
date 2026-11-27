# ⚡ AutoClip AI - Viral 9:16 Shorts Generator

**AutoClip AI** is an end-to-end desktop & web application that automatically transforms lengthy videos and live streams into **5 to 10 viral highlight short videos in 9:16 vertical format** (YouTube Shorts, TikTok, Instagram Reels) featuring **word-by-word highlighted subtitles** (Alex Hormozi & MrBeast style) and exported as ready-to-publish MP4 files.

---

## 🌟 Key Features

1. **Dual Ingestion**:
   - **YouTube & Live Stream URLs**: Directly paste any YouTube video or livestream replay URL via `yt-dlp`.
   - **Local Video Uploads**: Drag and drop MP4, MKV, MOV, WebM videos of any duration.

2. **Word-Level Precision Transcription**:
   - High-speed local speech-to-text powered by `faster-whisper` (CTranslate2).
   - Generates exact millisecond word timestamps (`[{word, start, end}]`).
   - Disk-cached for instant re-clipping without re-transcribing.

3. **Viral Highlight Detection (5–10 Clips)**:
   - **Smart Offline Heuristic Engine**: Zero API key required! Analyzes speech density, cadence, hook strength, curiosity triggers, and natural sentence pauses.
   - **AI/LLM Multi-Provider Support**: Plug in optional Gemini, Groq, or OpenAI API keys for contextual deep analysis.
   - Outputs virality rating (80–99%), catchy click-worthy titles, hook captions, and viral rationales.

4. **Dynamic Word-Highlighted Subtitles**:
   - **Alex Hormozi / MrBeast Style**: The currently spoken word dynamically glows in vibrant colors (`Neon Yellow`, `Electric Green`, `Vibrant Cyan`, `Sunset Coral`) while surrounding words stay crisp white with bold black outlines and drop shadow.
   - Perfectly sized 2–3 word mobile phrasing positioned safely above YouTube Shorts icons.
   - Burned directly into the MP4 video via FFmpeg `libass` vector rendering.
   - Also exports `.srt` and `.ass` subtitle files for manual editing.

5. **9:16 Visual Reframing**:
   - **Streamer above video** (default): The bundled OpenCV YuNet detector samples five frames and looks for a face that stays in a webcam overlay. The upper panel follows that location for the clip. The main scene, captions and opening title appear below. For the layout with a webcam above the minimap, you can also select **Webcam at middle left** explicitly. Auto mode uses that position if detection is inconclusive.
   - **Fit + Blurred Ambient Background** (Recommended): Scales original video to fit 1080px width while filling the top and bottom 9:16 canvas with a heavily blurred, darkened version of the video. Keeps 100% of video content visible!
   - **Direct Center Crop**: 9:16 crop directly from the center.
   - **Smart Face-Tracking Crop**: OpenCV face detection centers the crop window around the active speaker.

6. **Studio Web UI & 1-Click ZIP Download**:
   - Sleek dark-mode dashboard with real-time pipeline progress tracker.
   - 9:16 HTML5 video preview player for each clip.
   - One-click "Download All Shorts (ZIP)" pack.

---

## 🚀 Quick Start

### 1. Set up the app on another computer

Clone the repository or download and extract its ZIP from GitHub. Install Python 3.11 and add an FFmpeg build with `libx264` and `libass` support to your `PATH`. Both `ffmpeg -version` and `ffprobe -version` should work in a new terminal.

On Windows, run these commands in PowerShell from the project folder.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

On macOS or Linux, run these commands from the project folder.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> on the computer running the app. After setup on Windows, you can also double-click `run.bat` to launch it.

The first transcription may download a Whisper model. Uploads, cached transcripts, and exported clips are stored in the local `data/` folder on each computer. To open the app from a phone or another device without installing it there, run the FastAPI server on a computer or host that device can reach.

---

### 2. Command Line (CLI)
You can also run batch processing without opening the browser:
```powershell
.\.venv\Scripts\python cli.py --input "https://www.youtube.com/watch?v=EXAMPLE_ID" --clips 7 --subtitles neon_yellow --framing fit_blur
```
Or for a local video:
```powershell
.\.venv\Scripts\python cli.py --input "C:\path\to\podcast.mp4" --clips 10 --subtitles electric_green
```

---

## 📁 Project Structure

```
d:\Clipping\
├── app.py                     # FastAPI web backend with streaming endpoints
├── cli.py                     # Command-line interface for headless batch processing
├── config.py                  # Presets, default settings, and directories
├── run.bat                    # Windows 1-click launcher
├── requirements.txt           # Python package dependencies
├── core/
│   ├── downloader.py          # yt-dlp & ffprobe metadata handler
│   ├── transcriber.py         # Word-timestamp transcription with faster-whisper
│   ├── highlight_detector.py  # AI (Gemini/Groq/OpenAI) + local heuristic detection
│   ├── subtitles.py           # Word-highlighted ASS and SRT generator
│   ├── video_processor.py     # FFmpeg 9:16 reframer (fit-blur / crop) & subtitle burning
│   └── pipeline.py            # Orchestrator tying ingestion to export
├── static/
│   ├── css/style.css          # Dark-mode creator studio theme
│   ├── js/app.js              # Interactive SPA & polling engine
│   └── index.html             # Studio user interface
└── data/
    ├── uploads/               # Stored video uploads
    ├── cache/                 # Audio WAVs and transcripts cache
    └── outputs/               # Generated 9:16 MP4 clips, thumbnails, and ZIP packs
```

---

## 🎨 Subtitle Styles

- **Neon Yellow (Hormozi Style)**: Vibrant yellow highlight on active word, bold Arial Black text, thick black outline.
- **Electric Green (MrBeast Style)**: Bright green highlight on active word, high contrast.
- **Electric Cyan**: Cyberpunk cyan glow on active word.
- **Sunset Orange**: Warm coral/orange pop on active word.
