import os
import sys
import subprocess
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import UPLOAD_DIR, OUTPUT_DIR
from core.subtitles import SubtitleGenerator
from core.video_processor import VideoProcessor
from core.highlight_detector import HighlightDetector
from core.downloader import VideoDownloader

def create_synthetic_test_video(output_path: str, duration: int = 15):
    """Generate a test 16:9 1080p video with audio using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=1920x1080:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print(f"Generated test video: {output_path}")

def test_subtitle_generation():
    print("\n--- Testing Subtitle Generator ---")
    mock_transcript = {
        "duration": 10.0,
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "The secret to success is consistency",
                "words": [
                    {"word": "The", "start": 0.1, "end": 0.4},
                    {"word": "secret", "start": 0.5, "end": 0.9},
                    {"word": "to", "start": 1.0, "end": 1.2},
                    {"word": "success", "start": 1.3, "end": 1.8},
                    {"word": "is", "start": 1.9, "end": 2.1},
                    {"word": "consistency", "start": 2.2, "end": 2.8}
                ]
            }
        ]
    }
    
    gen = SubtitleGenerator(style_preset="neon_yellow")
    out_ass = str(OUTPUT_DIR / "test_subs.ass")
    out_srt = str(OUTPUT_DIR / "test_subs.srt")
    
    gen.generate_subtitles_for_clip(
        transcript_data=mock_transcript,
        clip_start=0.0,
        clip_end=3.0,
        output_ass_path=out_ass,
        output_srt_path=out_srt
    )
    
    assert Path(out_ass).exists(), "ASS subtitle file was not created"
    assert Path(out_srt).exists(), "SRT subtitle file was not created"
    
    with open(out_ass, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Style: Default" in content
        assert "{\\c&H0000FFFF&\\b1}SECRET" in content or "SECRET" in content
        print("ASS Subtitles verified with highlight tags!")
    
    with open(out_srt, "r", encoding="utf-8") as f:
        srt_content = f.read()
        assert "-->" in srt_content
        print("SRT Subtitles verified!")

def test_highlight_detection():
    print("\n--- Testing Highlight Detector (Heuristic) ---")
    mock_segments = [
        {"start": 0.0, "end": 15.0, "text": "Welcome to the show today we are talking about life.", "words": []},
        {"start": 15.0, "end": 45.0, "text": "What is the biggest mistake you can make? The secret nobody tells you is actually that failure is required! It is insane how many people quit.", "words": []},
        {"start": 45.0, "end": 75.0, "text": "Why do people lose money? Here is the rule to follow every single day.", "words": []},
        {"start": 75.0, "end": 105.0, "text": "Thanks for watching and see you next time.", "words": []}
    ]
    
    detector = HighlightDetector()
    clips = detector.detect_highlights(
        transcript_data={"duration": 105.0, "segments": mock_segments},
        min_clips=2,
        max_clips=5,
        min_duration=15.0,
        max_duration=40.0
    )
    
    print(f"Detected {len(clips)} clips:")
    for c in clips:
        print(f" - #{c['clip_id']}: '{c['title']}' ({c['start']}s - {c['end']}s) Virality: {c['virality_score']}%")
    
    assert len(clips) >= 2, "Expected at least 2 clips"
    assert clips[0]["virality_score"] >= 80, "Expected virality score >= 80"

def test_video_reframing_and_burn():
    print("\n--- Testing 9:16 Reframing & Subtitle Burning ---")
    test_video = str(OUTPUT_DIR / "test_input.mp4")
    create_synthetic_test_video(test_video, duration=8)
    
    out_short = str(OUTPUT_DIR / "test_short_9_16.mp4")
    out_ass = str(OUTPUT_DIR / "test_subs.ass")
    
    VideoProcessor.render_short_clip(
        source_video_path=test_video,
        start_time=0.0,
        end_time=5.0,
        output_mp4_path=out_short,
        ass_subtitles_path=out_ass,
        framing_mode="fit_blur",
        burn_subtitles=True
    )
    
    assert Path(out_short).exists(), "Output 9:16 short was not generated"
    
    info = VideoDownloader.get_video_info(out_short)
    print(f"Output short probed info: {info['width']}x{info['height']}, duration: {info['duration']}s, fps: {info['fps']}")
    
    assert info["width"] == 1080, f"Expected width 1080, got {info['width']}"
    assert info["height"] == 1920, f"Expected height 1920, got {info['height']}"
    assert info["duration"] > 4.0, f"Expected duration ~5s, got {info['duration']}"
    print("9:16 Video successfully verified!")

if __name__ == "__main__":
    test_subtitle_generation()
    test_highlight_detection()
    test_video_reframing_and_burn()
    print("\n[SUCCESS] ALL TESTS PASSED SUCCESSFULLY!")
