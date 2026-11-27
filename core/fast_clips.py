"""Prepare one clip at a time so rendering can begin before the rest of a job."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import yt_dlp

from config import CACHE_DIR
from core.exact_range import prepare_exact_range
from core.youtube_replay import download_replay_section, sequence_tracks
from core.downloader import VideoDownloader
from core.highlight_detector import VIRAL_HOOK_KEYWORDS
from core.transcriber import VideoTranscriber


def clip_starts(duration, count, heatmap, clip_seconds):
    length = min(clip_seconds, duration)
    count = min(count, max(1, int(duration // clip_seconds)))
    # Pick one moment per section of the recording to keep clips distinct.
    for index in range(count):
        left = duration * index / count
        right = duration * (index + 1) / count
        peaks = [p for p in heatmap if left <= p["start_time"] < right]
        anchor = max(peaks, key=lambda p: p.get("value", 0))["start_time"] if peaks else (left + right) / 2
        yield round(max(left, min(anchor - length / 2, right - length)), 3)


def fast_clips(source, count, model, keys, notify, captions=True, resolution=720, clip_seconds=60, time_range=None):
    transcriber = VideoTranscriber(model_size=model)
    remote = VideoDownloader.is_url(source)
    format_options = {"format": "best[height<=720][ext=mp4]/bestvideo[height<=720]+bestaudio/best"}
    if resolution == 1080:
        format_options = {"format": "bv*[height<=1080]+ba/b[height<=1080]/b",
                          "format_sort": ["res:1080", "vcodec:h264"]}
    with TemporaryDirectory(dir=os.environ.get("AUTOCLIP_JOB_TMP", CACHE_DIR)) as temp, yt_dlp.YoutubeDL({
        "quiet": True, "socket_timeout": 20, "noplaylist": True, "live_from_start": time_range is not None,
        **format_options,
        "outtmpl": str(Path(temp) / "%(section_number)s.%(ext)s"),
        "merge_output_format": "mp4",
    }) as ydl:
        info = ydl.extract_info(source, download=False) if remote else VideoDownloader.get_video_info(source)
        live = bool(info.get("is_live"))
        duration = float(info.get("duration") or 0)
        replay_tracks = sequence_tracks(info, allow_live=time_range is not None) if remote else []
        title = info.get("title") or Path(source).stem
        if not live and duration <= 0:
            raise ValueError("Could not determine the recording length. Check that FFmpeg and FFprobe are installed.")
        if time_range is not None:
            start, end = time_range
            if not live and end > duration:
                raise ValueError(f"End time exceeds the recording length ({duration:.1f} seconds).")
            if live and not replay_tracks:
                raise ValueError("This stream has no accessible rewind timeline. Try the saved replay.")
            if info.get("live_status") == "post_live" and not replay_tracks:
                raise ValueError("The full replay timeline is still processing. Try again when the replay is available.")
            starts, clip_seconds = [start], end - start
        else:
            starts = [0] * count if live else list(clip_starts(duration, count, info.get("heatmap") or [], clip_seconds))
        for index, start in enumerate(starts, 1):
            length = clip_seconds if live else min(duration, clip_seconds)
            notify(index, len(starts), "Preparing timestamp range" if time_range else f"Capturing {clip_seconds} seconds of live footage" if live else "Preparing selected clip")
            path, seek = source, start
            if remote and time_range is not None:
                path, seek = prepare_exact_range(
                    ydl, info, replay_tracks, start, start + length, temp,
                    lambda message: notify(index, len(starts), message))
            elif remote:
                media_id = (info.get("extractor_key"), info.get("id"), start, length, info.get("format_id"))
                seek = 0
                if replay_tracks:
                    media_id += ("dash-sequence-v1",)
                    seek = start - min((start // t["target_duration"]) * t["target_duration"] for t in replay_tracks)
                media_key = hashlib.sha256(repr(media_id).encode()).hexdigest()
                saved = CACHE_DIR / f"source_{media_key}.media"
                cache_media = not live
                if cache_media and saved.is_file():
                    path = str(saved)
                else:
                    if replay_tracks:
                        downloaded = Path(temp) / f"{index}.mp4"
                        notify(index, len(starts), "Downloading selected replay fragments")
                        seek = download_replay_section(ydl, replay_tracks, start, length, downloaded)
                    else:
                        section = dict(info, section_number=index, section_start=start, section_end=start + length)
                        ydl.process_info(section)
                        matches = [p for p in Path(temp).glob(f"{index}.*") if p.suffix in {".mp4", ".mkv", ".webm", ".ts"} and len(p.suffixes) == 1]
                        if not matches:
                            raise ValueError("The selected clip could not be downloaded.")
                        downloaded = matches[0]
                    path = str(downloaded)
                    if cache_media and VideoDownloader.get_video_info(path)["duration"] > 0:
                        try:
                            downloaded.rename(saved)
                        except FileExistsError:
                            pass
                        path = str(saved)
                actual = VideoDownloader.get_video_info(path)["duration"]
                if actual <= seek:
                    raise ValueError("The stream returned no playable video.")
                length = min(length, actual - seek)
            if not captions:
                yield path, {}, {"start": seek, "end": seek + length, "title": title, "hook": "",
                                 "live": live, "reason": "Selected excerpt", "virality_score": None}, start, title, duration, len(starts)
                continue
            identity = (info.get("extractor_key"), info.get("id")) if remote else (str(Path(path).resolve()), Path(path).stat().st_size, Path(path).stat().st_mtime_ns)
            identity = ("v4-exact" if time_range else "v3", identity, start, length, model,
                        bool(keys.get("groq")), (temp, index) if live and not time_range else "")
            key = hashlib.sha256(repr(identity).encode()).hexdigest()
            cache = CACHE_DIR / f"minute_{key}.json"
            notify(index, len(starts), "Preparing captions")
            previous = CACHE_DIR / f"{Path(source).stem}_transcript.json" if not remote else None
            if previous and previous.exists() and previous.stat().st_mtime >= Path(source).stat().st_mtime:
                # Captions from earlier full scans use source-relative timestamps.
                full = json.loads(previous.read_text(encoding="utf-8"))
                segments = []
                for segment in full.get("segments", []):
                    if segment["end"] <= start or segment["start"] >= start + length:
                        continue
                    def relative(item):
                        return {**item, "start": max(0, item["start"] - start), "end": min(length, item["end"] - start)}
                    words = [relative(w) for w in segment.get("words", []) if start < w["end"] and w["start"] < start + length]
                    segments.append({**relative(segment), "words": words, "text": " ".join(w["word"] for w in words) or segment["text"]})
                transcript = {"segments": segments}
            elif cache.exists():
                transcript = json.loads(cache.read_text(encoding="utf-8"))
            else:
                audio = Path(temp) / f"audio-{index}.wav"
                proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(seek), "-i", path, "-t", str(length),
                                       "-map", "0:a:0?", "-vn", "-ac", "1", "-ar", "16000", str(audio)], capture_output=True)
                if proc.returncode and not VideoDownloader.get_video_info(path)["has_audio"]:
                    transcript = {"segments": []}
                else:
                    proc.check_returncode()
                    transcript = transcriber.transcribe(str(audio), length, keys.get("groq"), prepared_audio=True)
                pending = Path(temp) / "captions.json"
                pending.write_text(json.dumps(transcript), encoding="utf-8")
                pending.replace(cache)
            phrases = [s["text"].strip() for s in transcript["segments"] if s["text"].strip()]
            def hook_score(phrase):
                return sum(weight for word, weight in VIRAL_HOOK_KEYWORDS.items() if word in phrase.lower()) + int("?" in phrase)
            hook = max(phrases, key=hook_score, default=title)
            words = re.split(r"(?<=[!?。])\s+", hook)[0].split()
            headline = " ".join(words[:9]).strip(" ,") + ("…" if len(words) > 9 else "")
            yield path, transcript, {"start": seek, "end": seek + length, "title": headline, "hook": hook,
                                     "live": live, "reason": ("Live capture" if live else "Selected excerpt from the recording") if phrases else "No speech recognized. Try the base transcription model for this clip.",
                                     "virality_score": None}, start, title, duration, len(starts)
