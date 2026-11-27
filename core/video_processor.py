import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import cv2

from core.webcam import find_webcam, LEFT_MIDDLE
from config import TARGET_WIDTH, TARGET_HEIGHT, OUTPUT_DIR

CPU_H264 = ("-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p")


@lru_cache(maxsize=1)
def fast_hd_encoder():
    """Probe real frames: FFmpeg lists encoders even when their drivers are absent."""
    for name, preset in (("h264_qsv", ("-preset", "veryfast")),
                         ("h264_nvenc", ("-preset", "p1")),
                         ("h264_amf", ("-quality", "speed"))):
        options = ("-c:v", name, *preset, "-pix_fmt", "nv12", "-b:v", "10M",
                   "-maxrate", "14M", "-bufsize", "20M")
        try:
            probe = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                 "color=size=1080x1920:rate=30", "-frames:v", "4", *options, "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            if probe.returncode == 0:
                return options
        except subprocess.TimeoutExpired:
            continue
    return CPU_H264


class VideoProcessor:
    """Renders 9:16 vertical videos with fit/crop reframing and burned highlighted subtitles."""

    @staticmethod
    def _escape_filter_path(filepath: str) -> str:
        """Escape Windows file paths for FFmpeg filter arguments."""
        # Convert backslashes to forward slashes and escape colon
        posix_path = Path(filepath).resolve().as_posix()
        # Escape ':' for FFmpeg filter syntax (e.g., C\: or D\:)
        escaped = posix_path.replace(":", r"\:")
        return escaped

    @staticmethod
    def detect_face_center_x(video_path: str, start_time: float, duration: float) -> Optional[int]:
        """Detect face center X coordinate in the video using OpenCV Haar cascade."""
        try:
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            face_cascade = cv2.CascadeClassifier(cascade_path)
            
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

            start_frame = int(start_time * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(start_frame, total_frames - 1))

            face_centers = []
            sample_interval = max(1, int(fps * 1.5)) # sample every 1.5s
            max_samples = 15
            samples_taken = 0

            while cap.isOpened() and samples_taken < max_samples:
                ret, frame = cap.read()
                if not ret:
                    break

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(60, 60))

                if len(faces) > 0:
                    # Pick largest face
                    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
                    x, y, w, h = faces[0]
                    face_centers.append(x + w // 2)

                samples_taken += 1
                # Skip to next sample
                cur_pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                cap.set(cv2.CAP_PROP_POS_FRAMES, cur_pos + sample_interval)

            cap.release()

            if face_centers:
                # Return median face center X
                face_centers.sort()
                median_x = face_centers[len(face_centers) // 2]
                return median_x
            return None
        except Exception as e:
            print(f"[VideoProcessor] Face detection error, using center crop: {e}")
            return None

    @classmethod
    def render_short_clip(
        cls,
        source_video_path: str,
        start_time: float,
        end_time: float,
        output_mp4_path: str,
        ass_subtitles_path: Optional[str] = None,
        framing_mode: str = "fit_blur",
        burn_subtitles: bool = True,
        fast_mode: bool = False,
        fast_resolution: int = 720
    ) -> str:
        """
        Extracts sub-clip, transforms to 9:16 (1080x1920), burns highlighted subtitles,
        and encodes to high quality MP4.
        """
        duration = max(1.0, end_time - start_time)
        filter_complex = cls._build_filter_complex(
            source_video_path=source_video_path,
            start_time=start_time,
            duration=duration,
            framing_mode=framing_mode,
            ass_subtitles_path=ass_subtitles_path if burn_subtitles else None,
            fast_mode=fast_mode, fast_resolution=fast_resolution
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(round(start_time, 3)),
            "-i", source_video_path,
            "-t", str(round(duration, 3)),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
        ]
        encoder = fast_hd_encoder() if fast_mode and fast_resolution == 1080 else CPU_H264
        output_options = [
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            output_mp4_path
        ]

        def encode(options):
            return subprocess.run([*cmd, *options, *output_options], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, text=True, errors="replace")
        result = encode(encoder)
        if result.returncode and encoder != CPU_H264:
            print(f"[VideoProcessor] {encoder[1]} failed; retrying with libx264.")
            result = encode(CPU_H264)
        if result.returncode != 0:
            if fast_mode or framing_mode.startswith("stacked"):
                raise RuntimeError(result.stderr[-1200:])
            print(f"[VideoProcessor] FFmpeg error: {result.stderr}")
            # Fallback simple command if complex filter had syntax issue
            cls._fallback_render(source_video_path, start_time, duration, output_mp4_path, ass_subtitles_path)

        return output_mp4_path

    @classmethod
    def _build_filter_complex(
        cls,
        source_video_path: str,
        start_time: float,
        duration: float,
        framing_mode: str,
        ass_subtitles_path: Optional[str],
        fast_mode: bool = False,
        fast_resolution: int = 720
    ) -> str:
        """Builds FFmpeg filter_complex string for 9:16 layout + subtitle overlay."""
        width, height = (720, 1280) if fast_mode and fast_resolution == 720 else (TARGET_WIDTH, TARGET_HEIGHT)
        source = "[0:v]fps=30," if fast_mode else "[0:v]"
        sub_filter = ""
        if ass_subtitles_path and Path(ass_subtitles_path).exists():
            escaped_ass = cls._escape_filter_path(ass_subtitles_path)
            sub_filter = f",ass='{escaped_ass}'"

        if framing_mode == "center_crop":
            # Crop 9:16 directly from center
            filter_chain = (
                f"{source}crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={width}:{height}{sub_filter}[v]"
            )
            return filter_chain

        elif framing_mode.startswith("stacked"):
            # Crop before scaling so the two branches resize only visible pixels.
            top_height = height * 3 // 8
            bottom_height = height - top_height
            corner = framing_mode.removeprefix("stacked_")
            face = find_webcam(source_video_path, start_time, duration) if corner == "stacked" else None
            x = "iw-ow" if "right" in corner else "0"
            y = "ih-oh" if "bottom" in corner else "0"
            top = (face or LEFT_MIDDLE) if corner in ("stacked", "left_middle") else f"crop=iw/3:ih/3:{x}:{y},"
            def panel(panel_height):
                ratio = width / panel_height
                return (f"crop='min(iw,ih*{ratio})':'min(ih,iw/{ratio})',"
                        f"scale={width}:{panel_height}:flags=fast_bilinear,setsar=1")
            return (f"{source}split=2[t][b];[t]{top}{panel(top_height)}[camera];"
                    f"[b]{panel(bottom_height)}[action];[camera][action]vstack{sub_filter}[v]")

        elif framing_mode == "smart_crop":
            # Try face detection
            face_x = cls.detect_face_center_x(source_video_path, start_time, duration)
            if face_x is not None:
                # Calculate crop x position centered on face
                filter_chain = (
                    f"{source}crop=ih*9/16:ih:min(max(0\\,{face_x}-(ih*9/32))\\,iw-(ih*9/16)):0,"
                    f"scale={width}:{height}{sub_filter}[v]"
                )
                return filter_chain
            else:
                # Default to center crop if no face detected
                return f"{source}crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={width}:{height}{sub_filter}[v]"

        else:
            # Mode: "fit_blur" (Optimized: 15x faster using low-res blur upscale + soft bokeh)
            # 1. Downscale background to 270x480 (16x fewer pixels), blur with light radius, scale back to 1080x1920
            # 2. Foreground: scale to width 1080 keeping original aspect ratio
            # 3. Overlay foreground centered on blurred background
            # 4. Burn subtitles on top
            filter_chain = (
                f"{source}split=2[bg_src][fg_src];"
                f"[bg_src]scale=270:480:force_original_aspect_ratio=increase,"
                f"crop=270:480,boxblur=8:2,scale={width}:{height}:flags=fast_bilinear,eq=brightness=-0.08[bg];"
                f"[fg_src]scale={width}:-1:flags=fast_bilinear[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2{sub_filter}[v]"
            )
            return filter_chain

    @classmethod
    def _fallback_render(
        cls,
        source_video: str,
        start_time: float,
        duration: float,
        output_path: str,
        ass_subtitles_path: Optional[str]
    ):
        """Fallback simple rendering pipeline."""
        vf = f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
        if ass_subtitles_path and Path(ass_subtitles_path).exists():
            escaped = cls._escape_filter_path(ass_subtitles_path)
            vf += f",ass='{escaped}'"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(round(start_time, 3)),
            "-i", source_video,
            "-t", str(round(duration, 3)),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def extract_thumbnail(cls, video_path: str, output_thumb_path: str, timestamp: float = 1.0) -> str:
        """Extract a high-quality frame thumbnail from the generated clip."""
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(round(timestamp, 2)),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_thumb_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_thumb_path
