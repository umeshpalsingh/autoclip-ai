"""Read bounded sections of a YouTube replay while it is still being archived."""
from concurrent.futures import ThreadPoolExecutor
from math import ceil, floor
from pathlib import Path
import io
import av
import subprocess
import time

from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import update_url_query


def sequence_tracks(info, allow_live=False):
    tracks = info.get("requested_formats") or [info]
    live_dvr = allow_live and info.get("is_live") and all(t.get("is_from_start") for t in tracks)
    if ((info.get("live_status") == "post_live" or live_dvr) and info.get("extractor_key") == "Youtube"
            and all(t.get("protocol") in ("http_dash_segments", "http_dash_segments_generator") and t.get("target_duration")
                    and t.get("ext") in ("m4a", "mp4") for t in tracks)):
        return tracks
    return []


def _read_fragment(ydl, track, number):
    request = Request(update_url_query(track["url"], {"sq": number}), headers=track.get("http_headers"))
    for attempt in range(3):
        try:
            with ydl.urlopen(request) as response:
                data = response.read()
            if data[4:8] != b"ftyp" or b"moof" not in data or b"mdat" not in data:
                raise ValueError("Incomplete replay fragment")
            return data
        except (RequestError, OSError, ValueError) as exc:
            if attempt == 2:
                raise ValueError(f"YouTube could not supply replay fragment {number}. Try again after the replay finishes processing.") from exc
            time.sleep(0.5 * (attempt + 1))


def _fragment_span(data):
    with av.open(io.BytesIO(data)) as fragment:
        packets = [p for p in fragment.demux() if p.pts is not None]
        if not packets:
            raise ValueError("This replay fragment has no usable timestamps.")
        return (min(float(p.pts * p.time_base) for p in packets),
                max(float((p.pts + (p.duration or 0)) * p.time_base) for p in packets))


def _write_exact_track(ydl, track, start, end, local, pool):
    # target_duration is an estimate. A long broadcast can drift several
    # fragments away from start / target_duration. Locate the start by PTS.
    step = track["target_duration"]
    number = floor(start / step)
    seen = set()
    while number >= 0 and number not in seen and len(seen) < 8:
        seen.add(number)
        data = _read_fragment(ydl, track, number)
        first, last = _fragment_span(data)
        if first <= start + 0.05 and last > start:
            break
        if first > start:
            number -= max(1, floor((first - start) / step))
        else:
            number += max(1, floor((start - last) / step))
    else:
        raise ValueError(f"The stream's available replay fragments do not cover the requested start ({start:g}s).")

    track_origin = first
    with local.open("wb") as stream:
        stream.write(data)
        while last < end - 0.005:
            count = max(1, min(4, floor((end - last) / step)))
            futures = [pool.submit(_read_fragment, ydl, track, n) for n in range(number + 1, number + count + 1)]
            for future in futures:
                data = future.result()
                first, next_end = _fragment_span(data)
                if next_end <= last or first > last + 0.1:
                    raise ValueError(f"The replay has a gap at {last:.2f}s inside the requested interval.")
                stream.write(data)
                last = next_end
            number += count
    return track_origin


def download_replay_section(ydl, tracks, start, length, destination, exact=False):
    """Remux the selected fragments; return the trim offset within the local file."""
    # Begin each track on its preceding fragment. Preserve relative audio/video
    # offsets when their fragment durations differ.
    origins = [floor(start / t["target_duration"]) * t["target_duration"] for t in tracks]
    measured = []
    inputs = []
    maps = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, (track, track_origin) in enumerate(zip(tracks, origins)):
            segment_length = track["target_duration"]
            first = floor(start / segment_length)
            end = ceil((start + length) / segment_length)
            local = Path(destination).with_suffix(f".track{index}.mp4")
            if exact:
                track_origin = _write_exact_track(ydl, track, start, start + length, local, pool)
            else:
                with local.open("wb") as stream:
                    for batch in range(first, end, 4):
                        futures = [pool.submit(_read_fragment, ydl, track, n) for n in range(batch, min(batch + 4, end))]
                        for future in futures:
                            stream.write(future.result())
            measured.append((track_origin, local))
            maps.extend(["-map", f"{index}:v?", "-map", f"{index}:a?"])
    origin = min(t for t, _ in measured)
    for track_origin, local in measured:
        if not exact:
            inputs.extend(["-itsoffset", str(track_origin - origin)])
        inputs.extend(["-i", str(local)])
    timestamps = ["-copyts", "-avoid_negative_ts", "disabled", "-use_editlist", "1"] if exact else []
    result = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *inputs, *maps,
                             "-c", "copy", *timestamps, str(destination)], capture_output=True)
    if result.returncode:
        raise ValueError("The replay fragments could not be combined into a playable clip.")
    return max(0, start - origin)
