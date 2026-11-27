"""Stage timestamped media for a single frame-accurate cut in the final render."""
import hashlib
import os
from pathlib import Path

import av

from config import CACHE_DIR
from core.youtube_replay import download_replay_section


class IncompleteRange(ValueError):
    pass


def trim_offset(path, start, end):
    # Container duration may include an empty leading edit or a longer audio
    # track. Check the actual packet timestamps on each track before publishing.
    with av.open(str(path)) as container:
        tracks = [s for s in container.streams if s.type in ("video", "audio")]
        if not container.streams.video or container.start_time is None:
            raise IncompleteRange("The selected range contains no playable video.")
        origin = container.start_time / av.time_base
        spans = {s.index: [float("inf"), float("-inf")] for s in tracks}
        for packet in container.demux(*tracks):
            if packet.pts is None or packet.is_discard:
                continue
            bounds = spans[packet.stream.index]
            bounds[0] = min(bounds[0], packet.pts)
            bounds[1] = max(bounds[1], packet.pts + (packet.duration or 0))
        for track in tracks:
            first, last = (float(value * track.time_base) for value in spans[track.index])
            if last < first:
                raise IncompleteRange(f"The downloaded section contains no {track.type} packets.")
            if first > start + 0.05 or last < end - 0.05:
                raise IncompleteRange(
                    f"Requested {start:g}–{end:g}s. The source supplied {track.type} "
                    f"from {first:.2f} to {last:.2f}s. Choose an available range or retry once the replay is ready.")
        return max(0.0, start - origin)


def prepare_exact_range(ydl, info, tracks, start, end, directory, notify):
    key = ("timestamped-v1", info.get("extractor_key"), info.get("id") or info.get("webpage_url"),
           info.get("format_id"), start, end, bool(tracks))
    saved = CACHE_DIR / f"source_{hashlib.sha256(repr(key).encode()).hexdigest()}.media"
    if saved.is_file():
        return str(saved), trim_offset(saved, start, end)

    if tracks:
        notify("Downloading selected replay fragments")
        path = Path(directory) / "range.mp4"
        download_replay_section(ydl, tracks, start, end - start, path, exact=True)
        seek = trim_offset(path, start, end)
    else:
        # Input seeking lands on keyframes. Preserve their original timestamps
        # and a small margin so the renderer can cut between them without a
        # second encode. Retry a short delivery once with a wider margin.
        for attempt, margin in enumerate((2, 12), 1):
            notify("Downloading timestamp range" if attempt == 1 else "Retrying with a wider download window")
            path = Path(directory) / f"{attempt}.mp4"
            options = dict(info.get("downloader_options") or {})
            options["ffmpeg_args_out"] = [*options.get("ffmpeg_args_out", ()), "-copyts", "-start_at_zero",
                                          "-avoid_negative_ts", "disabled", "-use_editlist", "1"]
            section = dict(info, section_number=attempt, ext="mp4", downloader_options=options,
                           section_start=max(0, start - margin), section_end=min(info["duration"], end + margin))
            ydl.process_info(section)
            try:
                seek = trim_offset(path, start, end)
                break
            except IncompleteRange:
                if attempt == 2 or (section["section_start"] == 0 and section["section_end"] == info["duration"]):
                    raise
    # A second job may publish the same range first. Its local trim offset
    # must then be read from the winning file.
    try:
        os.link(path, saved)
    except FileExistsError:
        return str(saved), trim_offset(saved, start, end)
    except OSError:
        return str(path), seek
    return str(saved), seek
