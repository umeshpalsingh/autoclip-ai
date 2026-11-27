import hashlib
import os
import re
import json
import uuid
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

from config import (
    UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR,
    DEFAULT_MIN_CLIPS, DEFAULT_MAX_CLIPS,
    DEFAULT_MIN_DURATION, DEFAULT_MAX_DURATION
)
from core.fast_clips import fast_clips
from core.downloader import VideoDownloader
from core.transcriber import VideoTranscriber
from core.highlight_detector import HighlightDetector
from core.subtitles import SubtitleGenerator
from core.video_processor import VideoProcessor

class ClippingPipeline:
    """End-to-end pipeline to clip lengthy videos/streams into 5-10 viral 9:16 shorts."""

    def __init__(self, job_id: Optional[str] = None):
        self.job_id = job_id or str(uuid.uuid4())[:8]
        self.job_dir = OUTPUT_DIR / self.job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.status = {
            "job_id": self.job_id,
            "stage": "idle",
            "percent": 0.0,
            "message": "Initialized",
            "video_title": "",
            "total_duration": 0.0,
            "clips": [],
            "zip_path": None,
            "error": None
        }

    def update_status(
        self,
        stage: str,
        percent: float,
        message: str,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        **extra
    ):
        self.status["stage"] = stage
        self.status["percent"] = round(percent, 1)
        self.status["message"] = message
        self.status.update(extra)
        if callback:
            callback(self.status)

    def process_video(
        self,
        source: str, # File path or URL
        clip_count: int = 1,
        min_duration: float = DEFAULT_MIN_DURATION,
        max_duration: float = DEFAULT_MAX_DURATION,
        framing_mode: str = "fit_blur",
        subtitle_style: str = "neon_yellow",
        burn_subtitles: bool = True,
        whisper_model: str = "tiny",
        api_keys: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        fast_mode: bool = False,
        subtitle_mode: Optional[str] = None,
        fast_resolution: int = 720,
        clip_seconds: Optional[int] = None,
        time_range: Optional[tuple[int, int]] = None
    ) -> Dict[str, Any]:
        """Runs the entire auto-clipping process from ingestion to 9:16 export."""
        try:
            if time_range is not None:
                start, end = time_range
                if not 0 <= start < end:
                    raise ValueError("End time must be after a nonnegative start time.")
                fast_mode = True
            if clip_seconds not in (None, 45, 60):
                raise ValueError("Clip length must be 45 or 60 seconds.")
            if fast_resolution not in (720, 1080):
                raise ValueError("Fast resolution must be 720 or 1080.")
            mode = subtitle_mode or ("burn" if burn_subtitles else "files")
            if mode not in {"burn", "files", "off"}:
                raise ValueError("Unknown subtitle mode")
            burn_subtitles = mode == "burn"
            # Stage 1: Ingestion / Download
            self.update_status("downloading", 5.0, "Preparing video source...", progress_callback)
            
            def full_scan():
                if VideoDownloader.is_url(source):
                    self.update_status("downloading", 10.0, "Downloading video from URL...", progress_callback)
                    dl_info = VideoDownloader.download_url(
                        source,
                        output_prefix=self.job_id,
                        progress_callback=lambda d: self.update_status(
                            "downloading",
                            10.0 + (d.get("percent", 0.0) * 0.15),
                            f"Downloading: {d.get('percent', 0.0)}% ({d.get('speed_mbps', 0)} MB/s)",
                            progress_callback
                        )
                    )
                    video_path = dl_info["filepath"]
                    video_title = dl_info.get("title", "Video Source")
                    duration = dl_info.get("duration", 0.0)
                else:
                    video_path = source
                    video_title = Path(source).stem
                    file_info = VideoDownloader.get_video_info(video_path)
                    duration = file_info.get("duration", 0.0)

                self.status["video_title"] = video_title
                self.status["total_duration"] = duration

                # Stage 2: Transcription with word-level timestamps
                self.update_status("transcribing", 25.0, "Extracting audio and transcribing...", progress_callback)
                keys = api_keys or {}
                transcriber = VideoTranscriber(model_size=whisper_model)
                transcript_data = transcriber.transcribe(
                    video_path=video_path,
                    total_duration=duration,
                    groq_api_key=keys.get("groq"),
                    progress_callback=lambda d: self.update_status(
                        "transcribing",
                        25.0 + (d.get("percent", 0.0) * 0.25),
                        d.get("status", "Transcribing speech..."),
                        progress_callback
                    )
                )

                # Stage 3: Detect 5-10 Highlights
                self.update_status("detecting_highlights", 55.0, f"Analyzing transcript for top {clip_count} viral moments...", progress_callback)
                keys = api_keys or {}
                detector = HighlightDetector(
                    gemini_api_key=keys.get("gemini"),
                    groq_api_key=keys.get("groq"),
                    openai_api_key=keys.get("openai")
                )
                highlights = detector.detect_highlights(
                    transcript_data=transcript_data,
                    min_clips=clip_count,
                    max_clips=clip_count,
                    min_duration=min_duration,
                    max_duration=clip_seconds or max_duration
                )

                if clip_seconds is not None:
                    available = duration or transcript_data.get("duration", 0)
                    length = min(clip_seconds, available)
                    if length > 0:
                        if not highlights and transcript_data.get("segments"):
                            first = transcript_data["segments"][0]
                            hook = first["text"].strip()
                            highlights = [{"start": first["start"], "title": " ".join(hook.split()[:9]) or video_title,
                                           "hook": hook, "reason": "Selected excerpt", "virality_score": None}]
                        fixed = {}
                        for h in highlights:
                            start = max(0, min(h["start"], available - length))
                            fixed.setdefault(start, {**h, "start": start, "end": start + length})
                        highlights = list(fixed.values())

                if not highlights:
                    raise ValueError("Could not extract any highlighted segments from this video.")

                self.update_status(
                    "detecting_highlights",
                    60.0,
                    f"Selected {len(highlights)} top highlights. Preparing 9:16 rendering...",
                    progress_callback
                )

                for highlight in highlights[:clip_count]:
                    yield video_path, transcript_data, highlight, highlight["start"], video_title, duration, len(highlights[:clip_count])

            def minute_progress(index, total, message):
                self.update_status("preparing", 5 + 90 * (index - 1) / total,
                                   f"{message} ({index}/{total})", progress_callback)

            items = fast_clips(source, clip_count, whisper_model, api_keys or {}, minute_progress, captions=mode != "off", resolution=fast_resolution, clip_seconds=clip_seconds or 60, time_range=time_range) if fast_mode else full_scan()
            # Stage 4: Subtitles & 9:16 Video Rendering
            sub_gen = SubtitleGenerator(style_preset=subtitle_style, custom_options={"title_margin": 750} if framing_mode.startswith("stacked") else None)
            rendered_clips = []


            for idx, (video_path, transcript_data, h, source_start, title, duration, total_clips) in enumerate(items, 1):
                self.status.update(video_title=title, total_duration=duration)
                clip_start = h["start"]
                clip_end = h["end"]
                clip_title = h["title"]
                safe_title = re.sub(r'[^a-zA-Z0-9_\- ]', '', clip_title).strip().replace(' ', '_')[:30]
                clip_filename = f"clip_{idx:02d}_{safe_title}.mp4"
                
                out_clip_path = str(self.job_dir / clip_filename)
                out_ass_path = str(self.job_dir / f"clip_{idx:02d}_{safe_title}.ass")
                out_srt_path = str(self.job_dir / f"clip_{idx:02d}_{safe_title}.srt")
                out_thumb_path = str(self.job_dir / f"clip_{idx:02d}_{safe_title}_thumb.jpg")

                # Generate subtitles
                if mode != "off":
                    sub_gen.generate_subtitles_for_clip(
                        transcript_data=transcript_data,
                        clip_start=0 if fast_mode else clip_start,
                        clip_end=clip_end - clip_start if fast_mode else clip_end,
                        title=clip_title,
                        output_ass_path=out_ass_path,
                        output_srt_path=out_srt_path
                    )

                # Update progress
                current_percent = (5 + (idx - 0.5) / total_clips * 90) if fast_mode else 60.0 + ((idx - 1) / total_clips) * 35.0
                self.update_status(
                    "rendering_clips",
                    current_percent,
                    f"Rendering 9:16 Short {idx} of {total_clips}: '{clip_title}'...",
                    progress_callback
                )

                # Render 9:16 MP4
                cached_mp4 = None
                if fast_mode:
                    media = Path(video_path)
                    signature = (str(media.resolve()), media.stat().st_size, media.stat().st_mtime_ns,
                                 clip_start, clip_end, framing_mode, burn_subtitles, f"fast{fast_resolution}-webcam-v2")
                    fingerprint = hashlib.sha256(repr(signature).encode())
                    if burn_subtitles:
                        fingerprint.update(Path(out_ass_path).read_bytes())
                    if not h.get("live") or time_range is not None:
                        cached_mp4 = CACHE_DIR / f"render_{fingerprint.hexdigest()}.mp4"
                if cached_mp4 and cached_mp4.exists():
                    try:
                        os.link(cached_mp4, out_clip_path)
                    except OSError:
                        shutil.copyfile(cached_mp4, out_clip_path)
                else:
                    VideoProcessor.render_short_clip(
                        source_video_path=video_path,
                        start_time=clip_start,
                        end_time=clip_end,
                        output_mp4_path=out_clip_path,
                        ass_subtitles_path=out_ass_path if burn_subtitles else None,
                        framing_mode=framing_mode,
                        burn_subtitles=burn_subtitles,
                        fast_mode=fast_mode, fast_resolution=fast_resolution
                    )

                    if cached_mp4:
                        try:
                            os.link(out_clip_path, cached_mp4)
                        except OSError:
                            pass  # Cache is optional on filesystems without hard links.

                # Generate thumbnail
                VideoProcessor.extract_thumbnail(out_clip_path, out_thumb_path, timestamp=min(1.5, (clip_end - clip_start) / 2))

                rendered_clips.append({
                    "clip_id": idx,
                    "title": clip_title,
                    "filename": clip_filename,
                    "mp4_url": f"/api/video/{self.job_id}/{clip_filename}",
                    "thumb_url": f"/api/video/{self.job_id}/{Path(out_thumb_path).name}",
                    **({
                        "srt_url": f"/api/video/{self.job_id}/{Path(out_srt_path).name}",
                        "ass_url": f"/api/video/{self.job_id}/{Path(out_ass_path).name}",
                    } if mode != "off" else {}),
                    "start": source_start,
                    "end": source_start + clip_end - clip_start,
                    "duration": round(clip_end - clip_start, 1),
                    "virality_score": h["virality_score"],
                    "hook": h["hook"],
                    "reason": h["reason"]
                })

                self.status["clips"] = rendered_clips.copy()
                if progress_callback:
                    progress_callback(self.status)

            # Stage 5: Create ZIP file for all clips
            zip_filename = f"{self.job_id}_shorts_pack.zip"
            zip_path = self.job_dir / zip_filename
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
                for c in rendered_clips:
                    mp4_file = self.job_dir / c["filename"]
                    if mp4_file.exists():
                        zipf.write(mp4_file, arcname=c["filename"])
                    srt_file = mp4_file.with_suffix(".srt")
                    if srt_file.exists():
                        zipf.write(srt_file, arcname=srt_file.name)

            self.status["clips"] = rendered_clips
            self.status["zip_url"] = f"/api/download-all/{self.job_id}"
            self.update_status(
                "completed",
                100.0,
                f"Successfully generated {len(rendered_clips)} viral shorts!",
                progress_callback,
                clips=rendered_clips,
                zip_url=f"/api/download-all/{self.job_id}"
            )

            # Save job metadata to json
            with open(self.job_dir / "job_manifest.json", "w", encoding="utf-8") as f:
                json.dump(self.status, f, indent=2)

            return self.status

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.status["stage"] = "error"
            self.status["error"] = str(e)
            self.update_status("error", 0.0, f"Error: {e}", progress_callback)
            return self.status
