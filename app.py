import os
import sys
import shutil
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, Literal
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure ffmpeg in PATH from system env if available
machine_path = os.environ.get("Path", "")
user_path = os.environ.get("Path", "")
if "ffmpeg" not in machine_path.lower() and "ffmpeg" not in user_path.lower():
    # Common winget location
    gyan_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for p in gyan_path.glob("**/ffmpeg.exe"):
        os.environ["PATH"] = str(p.parent) + ";" + os.environ.get("PATH", "")
        break

from config import (
    APP_NAME, HOST, PORT, UPLOAD_DIR, OUTPUT_DIR,
    FRAMING_MODES, SUBTITLE_STYLES,
    DEFAULT_MIN_CLIPS, DEFAULT_MAX_CLIPS,
    DEFAULT_MIN_DURATION, DEFAULT_MAX_DURATION
)
from core.studio_job import StudioJob
from core.downloader import VideoDownloader

app = FastAPI(title=APP_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory tracking of active jobs
jobs: Dict[str, StudioJob] = {}

class ProcessRequest(BaseModel):
    start_seconds: Optional[int] = Field(default=None, ge=0)
    end_seconds: Optional[int] = Field(default=None, gt=0)
    source_type: str # "upload" or "url"
    source_value: str # filename or youtube url
    clip_count: int = Field(default=1, ge=1, le=10)
    fast_mode: bool = True
    fast_resolution: Literal[720, 1080] = 720
    clip_seconds: Optional[Literal[45, 60]] = None
    min_duration: float = DEFAULT_MIN_DURATION
    max_duration: float = DEFAULT_MAX_DURATION
    framing_mode: str = "stacked"
    subtitle_style: str = "neon_yellow"
    burn_subtitles: bool = True
    subtitle_mode: Optional[Literal["burn", "files", "off"]] = None
    whisper_model: str = "tiny"
    gemini_key: Optional[str] = None
    groq_key: Optional[str] = None
    openai_key: Optional[str] = None

class ReRenderRequest(BaseModel):
    job_id: str
    clip_id: int
    start_time: float
    end_time: float
    subtitle_style: str = "neon_yellow"
    framing_mode: str = "stacked"

@app.get("/api/config")
def get_config():
    """Return available styles, framing modes, and defaults."""
    return {
        "app_name": APP_NAME,
        "framing_modes": FRAMING_MODES,
        "subtitle_styles": {k: v["name"] for k, v in SUBTITLE_STYLES.items()},
        "defaults": {
            "clip_count": 1,
            "fast_mode": True,
            "fast_resolution": 720,
            "clip_seconds": 60,
            "min_duration": DEFAULT_MIN_DURATION,
            "max_duration": DEFAULT_MAX_DURATION,
            "framing_mode": "stacked",
            "subtitle_style": "neon_yellow",
            "burn_subtitles": True
        }
    }

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a local video file (MP4, MKV, MOV, WebM, etc.)."""
    safe_name = file.filename.replace(" ", "_")
    target_path = UPLOAD_DIR / safe_name
    
    with open(target_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    info = VideoDownloader.get_video_info(str(target_path))
    return {
        "status": "success",
        "filename": safe_name,
        "filepath": str(target_path),
        "duration": info.get("duration", 0),
        "width": info.get("width", 0),
        "height": info.get("height", 0)
    }

def run_pipeline_task(pipeline: StudioJob, req: ProcessRequest, source_path_or_url: str):
    api_keys = {
        "gemini": req.gemini_key,
        "groq": req.groq_key,
        "openai": req.openai_key
    }
    pipeline.run(dict(
        source=source_path_or_url,
        clip_count=req.clip_count,
        min_duration=req.min_duration,
        max_duration=req.max_duration,
        framing_mode=req.framing_mode,
        subtitle_style=req.subtitle_style,
        burn_subtitles=req.burn_subtitles,
        subtitle_mode=req.subtitle_mode,
        whisper_model=req.whisper_model,
        fast_mode=req.fast_mode,
        fast_resolution=req.fast_resolution,
        clip_seconds=req.clip_seconds,
        time_range=(req.start_seconds, req.end_seconds) if req.start_seconds is not None else None,
        api_keys=api_keys
    ))

@app.post("/api/process")
async def process_video(req: ProcessRequest, background_tasks: BackgroundTasks):
    """Start the auto-clipping pipeline in background."""

    if (req.start_seconds is None) != (req.end_seconds is None):
        raise HTTPException(422, "Enter both a start time and an end time.")
    if req.start_seconds is not None and req.end_seconds <= req.start_seconds:
        raise HTTPException(422, "End time must be after start time.")
    if req.source_type == "upload":
        source_path = str(UPLOAD_DIR / req.source_value)
        if not Path(source_path).exists():
            raise HTTPException(status_code=404, detail="Uploaded file not found.")
        source_target = source_path
    else:
        if not VideoDownloader.is_url(req.source_value):
            raise HTTPException(status_code=400, detail="Invalid video URL.")
        source_target = req.source_value.strip()

    pipeline = StudioJob()
    jobs[pipeline.job_id] = pipeline
    # Launch task in background
    background_tasks.add_task(run_pipeline_task, pipeline, req, source_target)

    return {
        "status": "started",
        "job_id": pipeline.job_id
    }

@app.get("/api/status/{job_id}")
def get_job_status(job_id: str):
    """Poll the status and progress of a clipping job."""
    pipeline = jobs.get(job_id)
    if not pipeline:
        # Check if saved to disk manifest
        manifest_file = OUTPUT_DIR / job_id / "job_manifest.json"
        if manifest_file.exists():
            import json
            with open(manifest_file, "r", encoding="utf-8") as f:
                return json.load(f)
        raise HTTPException(status_code=404, detail="Job not found.")
    return pipeline.snapshot()

@app.post("/api/cancel/{job_id}")
def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return get_job_status(job_id)
    try:
        job.cancel()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.snapshot()

@app.get("/api/video/{job_id}/{filename}")
def stream_video(job_id: str, filename: str):
    """Stream video file or thumbnail with 206 partial content support."""
    file_path = OUTPUT_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Requested file not found.")
    
    media_type = "video/mp4"
    if filename.endswith(".jpg") or filename.endswith(".jpeg"):
        media_type = "image/jpeg"
    elif filename.endswith(".srt") or filename.endswith(".ass"):
        media_type = "text/plain"

    return FileResponse(file_path, media_type=media_type, filename=filename)

@app.get("/api/download-all/{job_id}")
def download_all_zip(job_id: str):
    """Download ZIP containing all clips."""
    zip_path = OUTPUT_DIR / job_id / f"{job_id}_shorts_pack.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP archive not found.")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"AutoClip_{job_id}_Viral_Shorts.zip"
    )

# Mount static directory for modern UI
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
def serve_index():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "AutoClip AI is running. Visit /static/index.html"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT, reload=True)
