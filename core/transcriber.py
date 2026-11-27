import os
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
import httpx

from config import CACHE_DIR, WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE

class VideoTranscriber:
    """Extracts audio and generates transcript with word-level timestamps (CPU optimized or Cloud)."""

    def __init__(self, model_size: str = WHISPER_MODEL, device: str = WHISPER_DEVICE, compute_type: str = WHISPER_COMPUTE_TYPE):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            num_threads = os.cpu_count() or 4
            print(f"[Transcriber] Loading faster-whisper '{self.model_size}' on {self.device} with {num_threads} threads...")
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=num_threads,
                num_workers=1,
                download_root=str(CACHE_DIR / "whisper_models")
            )
        return self._model

    @staticmethod
    def extract_audio(video_path: str, output_wav_path: Optional[str] = None) -> str:
        """Extract 16kHz mono WAV audio from video using FFmpeg."""
        if output_wav_path is None:
            base_name = Path(video_path).stem
            output_wav_path = str(Path(os.environ.get("AUTOCLIP_JOB_TMP", CACHE_DIR)) / f"{base_name}_audio.wav")

        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-threads", "0",
            output_wav_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_wav_path

    def transcribe(
        self,
        video_path: str,
        total_duration: float = 0.0,
        groq_api_key: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        prepared_audio: bool = False
    ) -> Dict[str, Any]:
        """
        Transcribes the video audio and returns full transcript with word-level timestamps.
        Caches results to disk so re-clipping is instantaneous.
        """
        cache_file = CACHE_DIR / f"{Path(video_path).stem}_transcript.json"
        if prepared_audio:
            cache_file = Path(video_path).with_suffix(".json")
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                print(f"[Transcriber] Loaded transcript from cache: {cache_file.name}")
                if progress_callback:
                    progress_callback({"stage": "transcribing", "percent": 100.0, "status": "Loaded from cache"})
                return cached_data
            except Exception as e:
                print(f"[Transcriber] Cache read error, re-transcribing: {e}")

        if progress_callback:
            progress_callback({"stage": "transcribing", "percent": 10.0, "status": "Extracting audio..."})

        audio_wav = video_path if prepared_audio else self.extract_audio(video_path)

        # 1. Check if Groq Cloud Whisper can be used (transcribes 8 mins in 2 seconds!)
        if groq_api_key:
            try:
                if progress_callback:
                    progress_callback({"stage": "transcribing", "percent": 30.0, "status": "Ultra-fast Groq Cloud transcription..."})
                cloud_data = self._transcribe_with_groq(audio_wav, groq_api_key)
                if cloud_data and cloud_data.get("segments"):
                    print("[Transcriber] Successfully transcribed using Groq Cloud Whisper.")
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(cloud_data, f, indent=2)
                    if progress_callback:
                        progress_callback({"stage": "transcribing", "percent": 100.0, "status": "Transcription complete"})
                    return cloud_data
            except Exception as e:
                print(f"[Transcriber] Groq cloud transcription error, falling back to local: {e}")

        # 2. Local Whisper with high-performance greedy search (beam_size=1)
        if progress_callback:
            progress_callback({"stage": "transcribing", "percent": 25.0, "status": "Transcribing speech & word timestamps..."})

        model = self._get_model()
        # beam_size=1 gives 4-5x speedup over beam_size=5 on CPU
        segments_generator, info = model.transcribe(
            audio_wav,
            beam_size=1,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400)
        )

        all_segments = []
        seg_id = 0
        video_dur = total_duration if total_duration > 0 else (info.duration or 1.0)

        for segment in segments_generator:
            words_data = []
            if segment.words:
                for w in segment.words:
                    words_data.append({
                        "word": w.word.strip(),
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "probability": round(w.probability, 3)
                    })

            all_segments.append({
                "id": seg_id,
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
                "words": words_data
            })
            seg_id += 1

            if progress_callback and video_dur > 0:
                current_time = segment.end
                trans_percent = 25.0 + min(70.0, (current_time / video_dur) * 70.0)
                progress_callback({
                    "stage": "transcribing",
                    "percent": round(trans_percent, 1),
                    "status": f"Transcribing ({round(current_time, 1)}s / {round(video_dur, 1)}s)"
                })

        if prepared_audio and not all_segments and info.duration_after_vad > 1:
            retry, _ = model.transcribe(audio_wav, beam_size=1, word_timestamps=False,
                                        temperature=0, condition_on_previous_text=False)
            all_segments = [{"start": s.start, "end": s.end, "text": s.text.strip(), "words": []}
                            for s in retry if s.text.strip()]

        result_data = {
            "language": info.language,
            "duration": round(info.duration, 2),
            "segments": all_segments
        }

        # Save to cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2)
        except Exception as e:
            print(f"[Transcriber] Failed to write cache: {e}")

        if progress_callback:
            progress_callback({"stage": "transcribing", "percent": 100.0, "status": "Transcription complete"})

        return result_data

    def _transcribe_with_groq(self, audio_path: str, api_key: str) -> Dict[str, Any]:
        """Call Groq Whisper-large-v3 API for near-instant transcription."""
        headers = {"Authorization": f"Bearer {api_key}"}
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        
        with open(audio_path, "rb") as f:
            files = {"file": (Path(audio_path).name, f, "audio/wav")}
            data = {
                "model": "whisper-large-v3",
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["word", "segment"]
            }
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, headers=headers, files=files, data=data)
                resp.raise_for_status()
                res = resp.json()

        segments = []
        raw_words = res.get("words", [])
        raw_segments = res.get("segments", [])

        if raw_segments:
            for s_idx, s in enumerate(raw_segments):
                s_start = float(s.get("start", 0))
                s_end = float(s.get("end", 0))
                s_words = [
                    {"word": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])}
                    for w in raw_words if w.get("start") >= s_start and w.get("end") <= s_end + 0.1
                ]
                segments.append({
                    "id": s_idx,
                    "start": s_start,
                    "end": s_end,
                    "text": s.get("text", "").strip(),
                    "words": s_words
                })
        return {
            "language": res.get("language", "en"),
            "duration": float(res.get("duration", 0)),
            "segments": segments
        }
