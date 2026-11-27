import os
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import SUBTITLE_STYLES, TARGET_WIDTH, TARGET_HEIGHT

def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp format: H:MM:SS.cs"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp format: HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

class SubtitleGenerator:
    """Generates dynamic word-highlighted ASS and SRT subtitle files for 9:16 Shorts."""

    def __init__(self, style_preset: str = "neon_yellow", custom_options: Optional[Dict[str, Any]] = None):
        preset = SUBTITLE_STYLES.get(style_preset, SUBTITLE_STYLES["neon_yellow"]).copy()
        if custom_options:
            preset.update(custom_options)
        self.config = preset

    def generate_subtitles_for_clip(
        self,
        transcript_data: Dict[str, Any],
        clip_start: float,
        clip_end: float,
        output_ass_path: str,
        output_srt_path: Optional[str] = None,
        title: str = ""
    ) -> str:
        """
        Extracts words in [clip_start, clip_end], offsets timestamps to start at 0.0,
        chunks them into short punchy mobile phrases, and writes out the ASS file.
        """
        words = []
        for seg in transcript_data.get("segments", []):
            if seg["end"] < clip_start or seg["start"] > clip_end:
                continue
            seg_words = seg.get("words", [])
            if seg_words:
                for w in seg_words:
                    if w["end"] > clip_start and w["start"] < clip_end:
                        words.append({
                            "word": w["word"],
                            "start": max(0.0, w["start"] - clip_start),
                            "end": min(clip_end, w["end"]) - clip_start
                        })
            else:
                # Fallback if segment didn't have word timestamps: estimate evenly
                seg_text = seg["text"].strip().split()
                if seg_text:
                    dur_per_word = (seg["end"] - seg["start"]) / len(seg_text)
                    for idx, txt in enumerate(seg_text):
                        w_s = seg["start"] + idx * dur_per_word
                        w_e = w_s + dur_per_word
                        if w_e > clip_start and w_s < clip_end:
                            words.append({
                                "word": txt,
                                "start": max(0.0, w_s - clip_start),
                                "end": min(clip_end, w_e) - clip_start
                            })

        if not words and not title:
            # Empty subtitle file if no words
            self._write_empty_ass(output_ass_path)
            return output_ass_path

        # Group words into short phrases (2 to 4 words per cue for mobile)
        phrases = self._group_words_into_phrases(words, max_words_per_phrase=3)

        # Generate ASS events
        ass_events = []
        if title:
            safe_title = " ".join(title.split()).translate(str.maketrans({"\\": "／", "{": "(", "}": ")"}))
            ass_events.append(f"Dialogue: 1,0:00:00.00,{format_ass_time(min(3, clip_end-clip_start))},Title,,0,0,0,,{safe_title}")
        for phrase in phrases:
            p_words = phrase["words"]
            # For each word in the phrase, emit a dialogue event highlighting that word
            for i, target_word in enumerate(p_words):
                w_start = target_word["start"]
                w_end = target_word["end"]
                if w_end <= w_start:
                    w_end = w_start + 0.25

                # Build styled line
                formatted_words = []
                for j, w in enumerate(p_words):
                    word_str = w["word"].upper() if self.config.get("uppercase", True) else w["word"]
                    if i == j:
                        # Highlight active word
                        hl_color = self.config["highlight_color"]
                        formatted_words.append(f"{{\\c{hl_color}\\b1}}{word_str}{{\\c{self.config['primary_color']}\\b1}}")
                    else:
                        formatted_words.append(word_str)

                line_text = " ".join(formatted_words)
                start_str = format_ass_time(w_start)
                end_str = format_ass_time(w_end)

                ass_events.append(
                    f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line_text}"
                )

        # Write ASS file
        self._write_ass_file(output_ass_path, ass_events)

        # Also write SRT if requested
        if output_srt_path:
            self._write_srt_file(output_srt_path, phrases)

        return output_ass_path

    def _group_words_into_phrases(
        self,
        words: List[Dict[str, Any]],
        max_words_per_phrase: int = 3
    ) -> List[Dict[str, Any]]:
        """Group sequential words into short phrases of 2-4 words, respecting pauses."""
        phrases = []
        current_chunk = []

        for i, w in enumerate(words):
            current_chunk.append(w)
            # Break if reached max words or pause > 0.45s before next word
            is_max = len(current_chunk) >= max_words_per_phrase
            is_pause = False
            if i + 1 < len(words):
                gap = words[i+1]["start"] - w["end"]
                if gap > 0.4:
                    is_pause = True
            is_end = (i == len(words) - 1)

            if is_max or is_pause or is_end:
                phrases.append({
                    "start": current_chunk[0]["start"],
                    "end": current_chunk[-1]["end"],
                    "words": current_chunk
                })
                current_chunk = []

        return phrases

    def _write_ass_file(self, filepath: str, events: List[str]):
        """Write complete ASS file with styles and dialogue events."""
        font_name = self.config.get("font_name", "Arial Black")
        font_size = self.config.get("font_size", 22)
        primary = self.config.get("primary_color", "&H00FFFFFF&")
        outline_col = self.config.get("outline_color", "&H00000000&")
        outline_w = self.config.get("outline_width", 4.5)
        shadow_w = self.config.get("shadow", 2.0)
        # MarginV=520 puts the text in the lower-middle safe zone (above YT Shorts controls)
        margin_v = self.config.get("margin_v", 520)

        header = f"""[Script Info]
Title: AutoClip Highlighted Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {TARGET_WIDTH}
PlayResY: {TARGET_HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary},&H000000FF,{outline_col},&H80000000,-1,0,0,0,100,100,1,0,1,{outline_w},{shadow_w},2,40,40,{margin_v},1

Style: Title,{font_name},76,{primary},&H000000FF,{outline_col},&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,70,70,{self.config.get("title_margin", 75)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header)
            for ev in events:
                f.write(ev + "\n")

    def _write_empty_ass(self, filepath: str):
        self._write_ass_file(filepath, [])

    def _write_srt_file(self, filepath: str, phrases: List[Dict[str, Any]]):
        """Write standard SRT subtitle file."""
        with open(filepath, "w", encoding="utf-8") as f:
            for idx, p in enumerate(phrases, 1):
                s_str = format_srt_time(p["start"])
                e_str = format_srt_time(p["end"])
                text = " ".join(w["word"].upper() if self.config.get("uppercase", True) else w["word"] for w in p["words"])
                f.write(f"{idx}\n{s_str} --> {e_str}\n{text}\n\n")
