"""Turn the raw screen recording into the submission MP4.

Speeds up the two "checking…" waits (so the video stays under 5 minutes on a slow machine),
trims the page-load lead-in and encodes H.264. The waits are found in the video itself (frames that
look like the progress screen), because the recorder's timeline drifts from wall-clock time when
the machine is busy; the recorder's wall-clock marks are only used to pick a reference frame.
Segments are encoded one at a time to keep memory low.

Usage:  python tools/make_video.py <dir with marks.json from record_demo.js> <out.mp4>
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

WAIT_SECONDS = 6.0   # how long each sped-up "checking" segment lasts in the final video
FPS = 4              # analysis frame rate
W, H = 160, 100


def frames(video: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps={FPS},scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def runs(mask: np.ndarray, min_len: float) -> list[tuple[float, float]]:
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if (i - start) / FPS >= min_len:
                out.append((start / FPS, i / FPS))
            start = None
    return out


def main() -> None:
    src_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    info = json.loads((src_dir / "marks.json").read_text())
    video, m = info["video"], info["marks"]
    a = frames(video)
    duration = len(a) / FPS
    ref = a[min(len(a) - 1, int((m["check1_start"] + m["check1_done"]) / 2 * FPS))]
    waits = runs(((a - ref) ** 2).mean(axis=(1, 2)) < 60, 2.0)[:2]
    cards = runs((a.std(axis=(1, 2)) < 25) & (a.mean(axis=(1, 2)) > 225), 0.5)
    start = max(0.0, cards[0][0] - 0.1) if cards else 0.0
    print("start", start, "waits", waits, "duration", duration)
    cuts, t = [], start
    for w0, w1 in waits:
        w0, w1 = w0 + 0.5, w1 - 0.5          # keep half a second of the progress screen at full speed
        cuts.append((t, w0, 1.0))
        cuts.append((w0, w1, max(1.0, (w1 - w0) / WAIT_SECONDS)))
        t = w1
    cuts.append((t, duration, 1.0))
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, (s0, s1, factor) in enumerate(cuts):
            seg = Path(tmp) / f"seg{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s0:.3f}", "-t", f"{s1 - s0:.3f}", "-i", video,
                            "-vf", f"setpts=(PTS-STARTPTS)/{factor:.4f},fps=25,format=yuv420p", "-an",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-tune", "stillimage",
                            "-threads", "2", str(seg)], check=True)
            files.append(seg)
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{f}'\n" for f in files))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        "-movflags", "+faststart", str(out)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True)
    print(f"{out}: {float(probe.stdout):.1f} s, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
