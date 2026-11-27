import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import av
from yt_dlp import YoutubeDL
from yt_dlp.downloader.external import FFmpegFD

from core.exact_range import IncompleteRange, prepare_exact_range, trim_offset
from core.youtube_replay import download_replay_section


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-nostdin", *map(str, args)])


class ExactRangeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.source = self.directory / "recording.mp4"
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=20",
               "-f", "lavfi", "-i", "sine=duration=20", "-c:v", "libx264",
               "-sc_threshold", "0", "-g", "20", "-c:a", "aac", self.source)

    def frames(self, path, offset, duration):
        return ffmpeg("-ss", offset, "-i", path, "-t", duration, "-map", "0:v:0",
                      "-f", "rawvideo", "-pix_fmt", "gray", "-")

    def test_vod_cut_between_keyframes_and_cached_repeat(self):
        with YoutubeDL({"quiet": True, "noprogress": True, "enable_file_urls": True,
                        "outtmpl": str(self.directory / "%(section_number)s.%(ext)s")}) as ydl:
            info = {"url": self.source.as_uri(), "title": "recording", "id": "fixture",
                    "extractor": "generic", "ext": "mp4", "protocol": "file", "duration": 20}
            with patch("core.exact_range.CACHE_DIR", self.directory), patch.object(
                    FFmpegFD, "SUPPORTED_PROTOCOLS", (*FFmpegFD.SUPPORTED_PROTOCOLS, "file")):
                def prepare():
                    return prepare_exact_range(ydl, info, [], 9, 14, self.directory, lambda _: None)
                path, seek = prepare()
                self.assertEqual(self.frames(path, seek, 5), self.frames(self.source, 9, 5))
                with patch.object(ydl, "process_info", side_effect=AssertionError("unexpected download")):
                    self.assertEqual((path, seek), prepare())
                with self.assertRaises(IncompleteRange):
                    trim_offset(path, 9, 19)

    def test_dvr_tracks_with_a_drifting_fragment_index(self):
        ffmpeg("-i", self.source, "-map", "0", "-c", "copy", "-f", "dash",
               "-seg_duration", "2", "-init_seg_name", "init-stream$RepresentationID$.m4s",
               "-media_seg_name", "chunk-stream$RepresentationID$-$Number%05d$.m4s",
               (self.directory / "stream.mpd").as_posix())
        # Each YouTube response includes the track header and one fragment.
        tracks = [{"index": i, "target_duration": 2.5} for i in (0, 1)]

        def read(_, track, index):
            prefix = self.directory / f"init-stream{track['index']}.m4s"
            body = self.directory / f"chunk-stream{track['index']}-{index+1:05}.m4s"
            return prefix.read_bytes() + body.read_bytes()

        path = self.directory / "range.mp4"
        with patch("core.youtube_replay._read_fragment", side_effect=read):
            download_replay_section(None, tracks, 11, 5, path, exact=True)
        seek = trim_offset(path, 11, 16)
        self.assertEqual(self.frames(path, seek, 5), self.frames(self.source, 11, 5))
        with av.open(str(path)) as container:
            self.assertEqual(len(container.streams.audio), 1)
