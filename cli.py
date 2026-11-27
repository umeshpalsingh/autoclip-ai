import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import FRAMING_MODES
from core.pipeline import ClippingPipeline

def timestamp(value):
    parts = value.split(":")
    if len(parts) > 3 or any(not n.isascii() or not n.isdigit() or (i and (len(n) > 2 or int(n) > 59))
                             for i, n in enumerate(parts)):
        raise argparse.ArgumentTypeError("Use HH:MM:SS, MM:SS, or whole seconds.")
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def main():
    parser = argparse.ArgumentParser(description="AutoClip AI: Clip big videos & livestreams into 5-10 viral 9:16 shorts with highlighted subtitles.")
    parser.add_argument("--input", "-i", required=True, help="Path to local video file or YouTube / livestream URL.")
    parser.add_argument("--clips", "-c", type=int, default=1, choices=range(1, 11), help="Number of clips to generate (default: 1).")
    parser.add_argument("--fast-resolution", type=int, choices=(720, 1080), default=720, help="Vertical export width in Fast mode.")
    parser.add_argument("--clip-seconds", type=int, choices=(45, 60), help="Export a fixed length (Fast mode defaults to 60 seconds).")
    parser.add_argument("--range", dest="time_range", nargs=2, type=timestamp, metavar=("START", "END"), help="Export exactly one interval, measured from the beginning.")
    parser.add_argument("--full-scan", action="store_true", help="Search the entire transcript for highlights.")
    parser.add_argument("--framing", "-f", choices=FRAMING_MODES, default="stacked", help="9:16 framing mode.")
    parser.add_argument("--subtitles", "-s", choices=["neon_yellow", "electric_green", "vibrant_cyan", "sunset_orange"], default="neon_yellow", help="Subtitle style preset.")
    captions = parser.add_mutually_exclusive_group()
    captions.add_argument("--no-subtitles", action="store_true", help="Export a clean video without subtitles or subtitle files.")
    captions.add_argument("--no-burn", action="store_true", help="Don't burn subtitles into video (exports separate .srt and .ass files instead).")
    parser.add_argument("--gemini-key", default=None, help="Gemini API Key for AI moment detection.")
    parser.add_argument("--groq-key", default=None, help="Groq API Key for AI moment detection.")
    parser.add_argument("--openai-key", default=None, help="OpenAI API Key for AI moment detection.")

    args = parser.parse_args()
    if args.time_range and args.time_range[0] >= args.time_range[1]:
        parser.error("End time must be after start time.")

    print("\n" + "="*60)
    print("⚡ AutoClip AI - Viral 9:16 Shorts Generator")
    print("="*60)
    print(f"Source:       {args.input}")
    print(f"Clips Target: {args.clips}")
    print(f"Framing:      {args.framing}")
    print(f"Subtitles:    {args.subtitles} (Burn: {not args.no_burn})")
    print("="*60 + "\n")

    pipeline = ClippingPipeline()

    def progress_callback(status):
        percent = status.get("percent", 0.0)
        msg = status.get("message", "")
        print(f"[{percent:5.1f}%] {msg}")

    api_keys = {
        "gemini": args.gemini_key,
        "groq": args.groq_key,
        "openai": args.openai_key
    }

    result = pipeline.process_video(
        source=args.input,
        clip_count=args.clips,
        framing_mode=args.framing,
        subtitle_style=args.subtitles,
        burn_subtitles=not args.no_burn,
        subtitle_mode="off" if args.no_subtitles else None,
        fast_mode=not args.full_scan,
        fast_resolution=args.fast_resolution,
        clip_seconds=args.clip_seconds,
        time_range=args.time_range,
        api_keys=api_keys,
        progress_callback=progress_callback
    )

    if result.get("stage") == "completed":
        print("\n" + "="*60)
        print("🎉 Successfully generated 9:16 Shorts!")
        print(f"Output Directory: {pipeline.job_dir}")
        print(f"Pack ZIP:         {pipeline.job_dir / f'{pipeline.job_id}_shorts_pack.zip'}")
        print("="*60)
        for clip in result.get("clips", []):
            print(f"- [#{clip['clip_id']}] {clip['title']} ({clip['duration']}s, Virality: {clip['virality_score']}%)")
            print(f"  File: {pipeline.job_dir / clip['filename']}")
        print("="*60 + "\n")
    else:
        print(f"\n❌ Error occurred: {result.get('error')}")

if __name__ == "__main__":
    main()
