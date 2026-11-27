"""Locate a persistent webcam face in the original landscape footage."""
from pathlib import Path

import cv2
import numpy as np


MODEL = str(Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx")

# Normalized webcam rectangle in the reference layout, above the minimap.
LEFT_MIDDLE = "crop=iw*0.200:ih*0.218:iw*0.004:ih*0.427,"


def _inset_edges(frame, face):
    """Look for straight horizontal webcam borders above and below the head."""
    x, y, w, h = face[:4]
    height, width = frame.shape[:2]
    left, right = max(0, int(x - 0.65*w)), min(width, int(x + 1.65*w))
    upper, lower = max(1, int(y - 2*h)), min(height, int(y + 3*h))
    strip = frame[upper-1:lower, left:right].astype(np.int16)
    if strip.shape[0] < 3 or not strip.shape[1]:
        return 0
    changes = np.abs(np.diff(strip, axis=0)).max(axis=2)
    lines = np.mean(changes > 18, axis=1)
    def strength(start, end):
        band = lines[max(0, int(start)-upper):max(0, int(end)-upper)]
        return float(band.max()) if band.size else 0
    return min(strength(y-2*h, y), strength(y+h, y+3*h))


def find_webcam(path, start, duration):
    capture = cv2.VideoCapture(str(path))
    tracks = []
    sampled = 0
    detector = None
    try:
        for fraction in (0.05, 0.23, 0.49, 0.72, 0.93):
            capture.set(cv2.CAP_PROP_POS_MSEC, 1000 * (start + fraction * duration))
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            if detector is None:
                # YuNet works well on small overlays at this size on CPU.
                dw = min(832, width)
                dh = max(32, round(height * dw / width))
                detector = cv2.FaceDetectorYN_create(MODEL, "", (dw, dh), 0.70)
            sample = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
            _, faces = detector.detect(sample)
            sampled += 1
            used = set()
            for face in ([] if faces is None else faces):
                x, y, fw, fh = face[:4]
                cx, cy = (x+fw/2)/dw, (y+fh/2)/dh
                # A webcam overlay occupies an edge of the source. Central
                # speakers and large close-ups usually belong to the main scene.
                edge = min(cx, 1-cx, cy, 1-cy)
                if edge > 0.32 or fh/dh > 0.32:
                    continue
                box = np.array([cx, cy, fw/dw, fh/dh])
                match = None
                nearest = float("inf")
                for i, track in enumerate(tracks):
                    center = np.median(track["boxes"], axis=0)
                    distance = np.linalg.norm(box[:2] - center[:2])
                    if i not in used and distance < min(nearest, max(0.045, center[3]*0.65)) and 0.5 < box[3]/center[3] < 2:
                        match, nearest = i, distance
                if match is None:
                    match = len(tracks)
                    tracks.append({"boxes": [], "scores": []})
                used.add(match)
                tracks[match]["boxes"].append(box)
                tracks[match]["scores"].append(float(face[-1]) + _inset_edges(sample, face) + 0.6*(1-edge/0.32))
    finally:
        capture.release()

    consistent = [t for t in tracks if len(t["boxes"]) >= min(sampled, 2)]
    if not consistent:
        return None
    best = max(consistent, key=lambda t: len(t["boxes"])/sampled + np.median(t["scores"]))
    cx, cy, fw, fh = np.median(best["boxes"], axis=0)
    # Keep the forehead, shoulders and some room for movement in a 3:2 panel.
    crop_h = min(height, width/1.5, max(2.15*fh*height, 2.8*fw*width/1.5))
    ch = max(2, int(crop_h)//2*2)
    cw = max(2, int(ch*1.5)//2*2)
    x = int(np.clip(cx*width-cw/2, 0, width-cw))//2*2
    y = int(np.clip((cy+0.10*fh)*height-ch/2, 0, height-ch))//2*2
    return f"crop={cw}:{ch}:{x}:{y},"
