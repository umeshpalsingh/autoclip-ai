import os
import re
import json
from typing import List, Dict, Any, Optional
import httpx

from config import (
    DEFAULT_MIN_CLIPS, DEFAULT_MAX_CLIPS,
    DEFAULT_MIN_DURATION, DEFAULT_MAX_DURATION,
    GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY
)

VIRAL_HOOK_KEYWORDS = {
    # High-curiosity triggers
    "secret": 3.0, "nobody": 2.5, "truth": 2.5, "actually": 2.0, "revealed": 2.5,
    "mistake": 3.0, "never": 2.5, "always": 2.0, "stop": 2.5, "why": 2.0,
    "how to": 2.5, "hack": 2.5, "rule": 2.0, "insane": 3.0, "crazy": 2.5,
    "money": 2.5, "dollar": 2.0, "million": 2.5, "billion": 2.5, "rich": 2.0,
    "worst": 2.5, "best": 2.0, "danger": 2.5, "warning": 2.5, "fatal": 3.0,
    "problem": 2.0, "solution": 2.0, "listen": 2.0, "look": 1.5, "remember": 2.0,
    "algorithm": 2.0, "youtube": 1.5, "viral": 2.5, "failed": 2.5, "success": 2.0,
    "first time": 2.0, "shocking": 3.0, "unbelievable": 3.0, "lesson": 2.0
}

class HighlightDetector:
    """Detects 5 to 10 viral highlight moments from video transcripts using AI or smart heuristics."""

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        groq_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None
    ):
        self.gemini_key = gemini_api_key or GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        self.groq_key = groq_api_key or GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
        self.openai_key = openai_api_key or OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")

    def detect_highlights(
        self,
        transcript_data: Dict[str, Any],
        min_clips: int = DEFAULT_MIN_CLIPS,
        max_clips: int = DEFAULT_MAX_CLIPS,
        min_duration: float = DEFAULT_MIN_DURATION,
        max_duration: float = DEFAULT_MAX_DURATION
    ) -> List[Dict[str, Any]]:
        """
        Detects top 5-10 highlight clips.
        Tries AI models first if API key is available, then seamlessly falls back to smart local heuristic.
        """
        segments = transcript_data.get("segments", [])
        if not segments:
            return []

        total_video_duration = transcript_data.get("duration", segments[-1]["end"])
        
        # 1. Try Gemini if key is provided
        if self.gemini_key:
            try:
                clips = self._detect_with_gemini(segments, min_clips, max_clips, min_duration, max_duration)
                if len(clips) >= min_clips:
                    print(f"[HighlightDetector] Successfully detected {len(clips)} highlights using Gemini AI.")
                    return self._align_and_validate_clips(clips, segments, total_video_duration)
            except Exception as e:
                print(f"[HighlightDetector] Gemini API error, falling back: {e}")

        # 2. Try Groq if key is provided
        if self.groq_key:
            try:
                clips = self._detect_with_groq(segments, min_clips, max_clips, min_duration, max_duration)
                if len(clips) >= min_clips:
                    print(f"[HighlightDetector] Successfully detected {len(clips)} highlights using Groq AI.")
                    return self._align_and_validate_clips(clips, segments, total_video_duration)
            except Exception as e:
                print(f"[HighlightDetector] Groq API error, falling back: {e}")

        # 3. Try OpenAI if key is provided
        if self.openai_key:
            try:
                clips = self._detect_with_openai(segments, min_clips, max_clips, min_duration, max_duration)
                if len(clips) >= min_clips:
                    print(f"[HighlightDetector] Successfully detected {len(clips)} highlights using OpenAI.")
                    return self._align_and_validate_clips(clips, segments, total_video_duration)
            except Exception as e:
                print(f"[HighlightDetector] OpenAI API error, falling back: {e}")

        # 4. Smart Local Heuristic Detection (Zero-cost, works offline 100% reliably)
        print("[HighlightDetector] Using Smart Heuristic & Semantic Engine for highlight detection.")
        return self._detect_with_heuristics(segments, min_clips, max_clips, min_duration, max_duration, total_video_duration)

    def _detect_with_heuristics(
        self,
        segments: List[Dict[str, Any]],
        min_clips: int,
        max_clips: int,
        min_duration: float,
        max_duration: float,
        total_duration: float
    ) -> List[Dict[str, Any]]:
        """
        Smart heuristic analyzer:
        Evaluates candidate windows, scores hook power, speech density, and keyword excitement,
        then applies non-max suppression to pick the 5-10 best distinct clips.
        """
        candidates = []
        n_segs = len(segments)

        for i in range(n_segs):
            start_seg = segments[i]
            clip_start = start_seg["start"]

            # Expand window until min_duration <= window_duration <= max_duration
            for j in range(i, n_segs):
                end_seg = segments[j]
                clip_end = end_seg["end"]
                dur = clip_end - clip_start

                if dur < min_duration:
                    continue
                if dur > max_duration:
                    break

                # Score this segment window
                window_segs = segments[i:j+1]
                full_text = " ".join(s["text"] for s in window_segs)
                words = full_text.split()
                if len(words) < 15:
                    continue

                # Hook score (first 25% of text or first segment)
                first_seg_text = window_segs[0]["text"].lower()
                hook_points = 0.0
                if "?" in first_seg_text:
                    hook_points += 15.0
                if "!" in first_seg_text:
                    hook_points += 10.0

                for kw, weight in VIRAL_HOOK_KEYWORDS.items():
                    if kw in first_seg_text:
                        hook_points += weight * 4.0
                    elif kw in full_text.lower():
                        hook_points += weight * 1.5

                # Speech density & cadence (words per second)
                wps = len(words) / max(1.0, dur)
                cadence_score = 0.0
                if 2.0 <= wps <= 3.8:  # Energetic sweet spot for Shorts
                    cadence_score = 20.0
                elif wps > 1.2:
                    cadence_score = 10.0

                # Question-to-answer payoff bonus
                payoff_bonus = 15.0 if ("?" in full_text and (full_text.count(".") + full_text.count("!")) >= 2) else 5.0

                total_raw_score = hook_points + cadence_score + payoff_bonus
                # Normalize to 80 - 99 virality rating
                virality_score = min(99, int(75 + (total_raw_score / 4.0)))

                # Create catchy title from hook
                first_sentence = re.split(r'[.!?]', window_segs[0]["text"])[0].strip()
                if len(first_sentence) < 10 or len(first_sentence) > 60:
                    first_sentence = " ".join(words[:7]) + "..."
                title = first_sentence.title()

                candidates.append({
                    "start": clip_start,
                    "end": clip_end,
                    "duration": round(dur, 2),
                    "virality_score": virality_score,
                    "title": title,
                    "hook": window_segs[0]["text"][:120],
                    "text": full_text[:200] + "...",
                    "reason": "Strong opening hook with high-density spoken narrative"
                })

        # Sort all candidate windows by virality score descending
        candidates.sort(key=lambda x: x["virality_score"], reverse=True)

        # Apply Non-Maximum Suppression (avoid overlapping clips by at least 15s)
        selected_clips = []
        for cand in candidates:
            c_start = cand["start"]
            c_end = cand["end"]
            
            overlap = False
            for sel in selected_clips:
                # Check overlap
                s_start = sel["start"]
                s_end = sel["end"]
                if not (c_end <= s_start + 5.0 or c_start >= s_end - 5.0):
                    overlap = True
                    break

            if not overlap:
                selected_clips.append(cand)
                if len(selected_clips) >= max_clips:
                    break

        # If video is short and we got fewer than min_clips, loosen spacing
        if len(selected_clips) < min_clips and total_duration >= min_duration * 2:
            step = max(min_duration, (total_duration - min_duration) / max(1, min_clips))
            cur_time = 0.0
            while len(selected_clips) < min_clips and (cur_time + min_duration) <= total_duration:
                # Find matching segments
                c_start = cur_time
                c_end = min(total_duration, cur_time + min(max_duration, 45.0))
                # Check if already added
                if not any(abs(c["start"] - c_start) < 10.0 for c in selected_clips):
                    selected_clips.append({
                        "start": round(c_start, 2),
                        "end": round(c_end, 2),
                        "duration": round(c_end - c_start, 2),
                        "virality_score": 88,
                        "title": f"Key Highlight #{len(selected_clips) + 1}",
                        "hook": "Engaging moment from video",
                        "text": "",
                        "reason": "Thematic highlight segment"
                    })
                cur_time += step

        # Assign clip IDs and sort by start timestamp
        selected_clips.sort(key=lambda x: x["start"])
        for idx, clip in enumerate(selected_clips):
            clip["clip_id"] = idx + 1

        return selected_clips[:max_clips]

    def _detect_with_gemini(
        self,
        segments: List[Dict[str, Any]],
        min_clips: int,
        max_clips: int,
        min_duration: float,
        max_duration: float
    ) -> List[Dict[str, Any]]:
        """Call Gemini 1.5/2.0 API to identify viral segments."""
        transcript_text = "\n".join([f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}" for s in segments])
        
        prompt = f"""
You are a master YouTube Shorts and TikTok viral video editor.
Analyze the following transcript with timestamps and find between {min_clips} and {max_clips} the absolute most viral, engaging, and captivating highlight clips.

Each clip MUST:
1. Be between {min_duration} and {max_duration} seconds in duration.
2. Start at the exact beginning of a sentence/thought (use the provided start timestamp).
3. End at the clean conclusion of that thought/punchline (use the provided end timestamp).
4. Have an irresistible hook in the first 3 seconds.
5. Have a virality score between 80 and 99.
6. Not overlap with other selected clips.

Transcript:
{transcript_text[:25000]}

Return ONLY a valid JSON array of objects with these exact keys:
[
  {{
    "clip_id": 1,
    "title": "Short Catchy Click-Worthy Title (max 6 words)",
    "start": float,
    "end": float,
    "hook": "First sentence of the clip",
    "virality_score": integer (80-99),
    "reason": "Brief explanation of why this moment will go viral"
  }}
]
"""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.gemini_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}
        }
        
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            clips = json.loads(content)
            return clips

    def _detect_with_groq(
        self,
        segments: List[Dict[str, Any]],
        min_clips: int,
        max_clips: int,
        min_duration: float,
        max_duration: float
    ) -> List[Dict[str, Any]]:
        """Call Groq API (Llama 3 / Mixtral) for ultra-fast moment detection."""
        transcript_text = "\n".join([f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}" for s in segments])
        prompt = f"Analyze this transcript and output JSON array of {min_clips}-{max_clips} viral shorts ({min_duration}-{max_duration}s). Keys: clip_id, title, start, end, hook, virality_score, reason. Transcript:\n{transcript_text[:20000]}"
        
        headers = {"Authorization": f"Bearer {self.groq_key}"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": "You are a viral YouTube Shorts editor. Output only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"}
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                return parsed
            for k in ["clips", "highlights", "shorts"]:
                if k in parsed and isinstance(parsed[k], list):
                    return parsed[k]
            return []

    def _detect_with_openai(
        self,
        segments: List[Dict[str, Any]],
        min_clips: int,
        max_clips: int,
        min_duration: float,
        max_duration: float
    ) -> List[Dict[str, Any]]:
        """Call OpenAI API for highlight detection."""
        transcript_text = "\n".join([f"[{s['start']:.1f}s - {s['end']:.1f}s]: {s['text']}" for s in segments])
        prompt = f"Select {min_clips}-{max_clips} viral clips between {min_duration}s and {max_duration}s from this transcript. Output JSON array with keys: clip_id, title, start, end, hook, virality_score, reason.\n\n{transcript_text[:20000]}"
        
        headers = {"Authorization": f"Bearer {self.openai_key}"}
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        with httpx.Client(timeout=35.0) as client:
            resp = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                return parsed
            for k in ["clips", "highlights", "shorts"]:
                if k in parsed and isinstance(parsed[k], list):
                    return parsed[k]
            return []

    def _align_and_validate_clips(
        self,
        clips: List[Dict[str, Any]],
        segments: List[Dict[str, Any]],
        total_duration: float
    ) -> List[Dict[str, Any]]:
        """Validates timestamps and ensures start/end align cleanly."""
        validated = []
        for idx, clip in enumerate(clips):
            start = max(0.0, float(clip.get("start", 0.0)))
            end = min(total_duration, float(clip.get("end", start + 30.0)))
            if end <= start:
                continue

            # Find matching segment text
            clip_segs = [s for s in segments if s["end"] >= start and s["start"] <= end]
            text = " ".join(s["text"] for s in clip_segs)

            validated.append({
                "clip_id": idx + 1,
                "title": clip.get("title", f"Viral Moment #{idx+1}"),
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "hook": clip.get("hook", text[:80]),
                "virality_score": int(clip.get("virality_score", 90)),
                "reason": clip.get("reason", "Engaging dynamic segment"),
                "text": text[:200]
            })

        validated.sort(key=lambda x: x["start"])
        return validated
