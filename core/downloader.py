import os
import re
import json
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Callable
import yt_dlp

from config import UPLOAD_DIR, CACHE_DIR

class VideoDownloader:
    """Handles downloading YouTube videos, live streams, and inspecting video metadata."""

    @staticmethod
    def is_url(path_or_url: str) -> bool:
        """Check if the input string is a valid web URL."""
        return bool(re.match(r'^(http|https)://', path_or_url.strip()))

    @staticmethod
    def get_video_info(video_path: str) -> Dict[str, Any]:
        """Inspect video file metadata using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                video_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            data = json.loads(result.stdout)
            
            # Find primary video stream
            video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
            format_info = data.get("format", {})

            duration = float(format_info.get("duration", 0.0) or video_stream.get("duration", 0.0))
            width = int(video_stream.get("width", 1920))
            height = int(video_stream.get("height", 1080))
            fps_str = video_stream.get("r_frame_rate", "30/1")
            
            try:
                num, den = fps_str.split('/')
                fps = round(float(num) / float(den), 2)
            except Exception:
                fps = 30.0

            return {
                "duration": duration,
                "width": width,
                "height": height,
                "fps": fps,
                "has_audio": bool(audio_stream),
                "format_name": format_info.get("format_name", "mp4"),
                "bit_rate": format_info.get("bit_rate", None)
            }
        except Exception as e:
            print(f"[VideoDownloader] ffprobe error: {e}")
            return {
                "duration": 0.0,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
                "has_audio": True,
                "format_name": "mp4"
            }

    @staticmethod
    def download_url(
        url: str,
        output_prefix: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """
        Download YouTube video or livestream replay using yt-dlp.
        Returns a dictionary with filepath, title, duration, thumbnail, etc.
        """
        out_tmpl = str(Path(os.environ.get("AUTOCLIP_JOB_TMP", UPLOAD_DIR)) / f"{output_prefix}_%(title).50s.%(ext)s")

        def ytdl_hook(d):
            if progress_callback and d.get('status') == 'downloading':
                total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                downloaded = d.get('downloaded_bytes', 0)
                percent = min(100.0, (downloaded / total_bytes) * 100.0)
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                progress_callback({
                    "stage": "downloading",
                    "percent": round(percent, 1),
                    "speed_mbps": round((speed or 0) / 1024 / 1024, 2),
                    "eta_seconds": eta
                })

        ydl_opts = {
            'format': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': out_tmpl,
            'merge_output_format': 'mp4',
            'progress_hooks': [ytdl_hook],
            'quiet': True, 'noprogress': True,
            'no_warnings': True,
            'noplaylist': True,
            # Ensure livestreams dump cleanly
            'live_from_start': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # Check for mp4 extension after merge
            final_path = Path(filename).with_suffix(".mp4")
            if not final_path.exists() and Path(filename).exists():
                final_path = Path(filename)

            if os.environ.get("AUTOCLIP_JOB_TMP"):
                # Publish complete downloads only, retaining the source for reuse.
                saved = UPLOAD_DIR / final_path.name
                final_path.replace(saved)
                final_path = saved

            duration = info.get('duration', 0)
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', '')

            # Probe exact info on final file
            file_info = VideoDownloader.get_video_info(str(final_path))
            if duration == 0:
                duration = file_info.get("duration", 0)

            return {
                "filepath": str(final_path),
                "title": title,
                "duration": duration,
                "thumbnail": thumbnail,
                "width": file_info.get("width", 1920),
                "height": file_info.get("height", 1080),
                "fps": file_info.get("fps", 30.0)
            }
