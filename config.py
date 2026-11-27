import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
CACHE_DIR = DATA_DIR / "cache"

for d in [DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Application Settings
APP_NAME = "AutoClip AI - Viral Shorts Generator"
HOST = "127.0.0.1"
PORT = 8000

# Video Settings
DEFAULT_ASPECT_RATIO = "9:16"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

# Highlight Clip Defaults
DEFAULT_MIN_CLIPS = 5
DEFAULT_MAX_CLIPS = 10
DEFAULT_MIN_DURATION = 25  # seconds
DEFAULT_MAX_DURATION = 60  # seconds

# Framing Modes
FRAMING_MODES = {
    "stacked": "Streamer Above + Video Below (Stacked Split Screen)",
    "stacked_left_middle": "Webcam at middle left (above minimap)",
    **{f"stacked_{corner}": f"Camera at {corner.replace('_', ' ')}"
       for corner in ("top_left", "top_right", "bottom_left", "bottom_right")},
    "fit_blur": "Fit with Blurred Background (Recommended - Keeps 100% of video content)",
    "center_crop": "Direct Center Crop (Fills 9:16 screen)",
    "smart_crop": "Smart Face-Tracking Crop (Centers active speaker)"
}

# Subtitle Styling Presets
SUBTITLE_STYLES = {
    "neon_yellow": {
        "name": "Neon Yellow (Hormozi Style)",
        "highlight_color": "&H0000FFFF&",  # BGR format in ASS: Yellow is B:00, G:FF, R:FF
        "primary_color": "&H00FFFFFF&",    # White
        "outline_color": "&H00000000&",    # Black
        "font_name": "Arial Black",
        "font_size": 64,
        "outline_width": 4.0,
        "shadow": 2.0,
        "uppercase": True
    },
    "electric_green": {
        "name": "Electric Green (MrBeast Style)",
        "highlight_color": "&H0033FF33&",  # Bright Green
        "primary_color": "&H00FFFFFF&",
        "outline_color": "&H00000000&",
        "font_name": "Arial Black",
        "font_size": 64,
        "outline_width": 4.0,
        "shadow": 2.0,
        "uppercase": True
    },
    "vibrant_cyan": {
        "name": "Electric Cyan",
        "highlight_color": "&H00FFFF00&",  # Cyan in BGR (B:FF, G:FF, R:00)
        "primary_color": "&H00FFFFFF&",
        "outline_color": "&H00000000&",
        "font_name": "Impact",
        "font_size": 64,
        "outline_width": 4.0,
        "shadow": 2.0,
        "uppercase": True
    },
    "sunset_orange": {
        "name": "Sunset Coral / Orange",
        "highlight_color": "&H000080FF&",  # Orange in BGR
        "primary_color": "&H00FFFFFF&",
        "outline_color": "&H00000000&",
        "font_name": "Arial Black",
        "font_size": 64,
        "outline_width": 4.0,
        "shadow": 2.0,
        "uppercase": True
    }
}

# Whisper Model Settings
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")  # tiny, base, small, medium
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu") # cpu or cuda
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8") # int8 or float16

# AI Highlight Detection API Keys (optional; falls back to smart local heuristic if not provided)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
