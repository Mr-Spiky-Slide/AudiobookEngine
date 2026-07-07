#!/usr/bin/env python3
"""
chapter_engine.py — v1

Takes a raw, chapterless audio file (mp3/wav/m4a/etc.) and produces an
.m4b with real chapter markers, using silence detection to propose
chapter breaks and an interactive review step before anything is
finalized (nothing is written until you confirm).

Requires: ffmpeg + ffprobe on your PATH (brew install ffmpeg)

Usage:
    python chapter_engine.py input.mp3 -o output.m4b
    python chapter_engine.py input.mp3 -o output.m4b --min-gap 1.5 --min-chapter-len 180

v2 idea (not in this version): swap/augment silence detection with a
Whisper transcript + topic-shift heuristic for recordings where pauses
don't line up with real chapter boundaries.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd):
    """Run a command, return (stdout, stderr, returncode)."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


def check_dependencies():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        sys.exit(
            f"Missing required tool(s): {', '.join(missing)}. "
            "Install ffmpeg (e.g. `brew install ffmpeg`) and make sure it's on your PATH."
        )


def get_duration(path):
    out, err, code = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ])
    if code != 0 or not out.strip():
        sys.exit(f"ffprobe failed to read duration of {path}:\n{err.strip()}")
    try:
        return float(out.strip())
    except ValueError:
        sys.exit(f"Could not parse duration from ffprobe output: {out!r}")


def detect_silences(path, noise_db, min_silence_len):
    """Run ffmpeg's silencedetect filter and parse start/end timestamps."""
    _, err, code = run([
        "ffmpeg", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_len}",
        "-f", "null", "-"
    ])
    if code != 0:
        sys.exit(f"ffmpeg silence detection failed on {path}:\n{err.strip()}")

    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", err)]

    # pair them up (ffmpeg logs start then end in order). If the file ends
    # while still silent, the trailing silence_start has no matching end —
    # zip() drops it, which is fine since we don't want a break right at EOF.
    gaps = list(zip(starts, ends))
    return gaps


def propose_breaks(gaps, duration, min_chapter_len):
    """Turn silence gaps into candidate chapter-break timestamps.

    Uses the midpoint of each silence gap, and filters out candidates
    that would create a chapter shorter than min_chapter_len.
    """
    candidates = [(s + e) / 2 for s, e in gaps]
    breaks = [0.0]
    for c in candidates:
        if c - breaks[-1] >= min_chapter_len and duration - c >= min_chapter_len:
            breaks.append(c)
    return breaks


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def review_breaks(breaks, duration):
    """Interactively confirm/drop/adjust each proposed break."""
    confirmed = [0.0]
    for b in breaks[1:]:
        while True:
            resp = input(
                f"\nProposed chapter break at {fmt_time(b)} "
                f"(chapter would be {fmt_time(b - confirmed[-1])} long) "
                f"— keep? [y/n/type new mm:ss]: "
            ).strip().lower()
            if resp == "y":
                confirmed.append(b)
                break
            elif resp == "n":
                break
            elif re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", resp):
                parts = [int(p) for p in resp.split(":")]
                if len(parts) == 2:
                    new_t = parts[0] * 60 + parts[1]
                else:
                    new_t = parts[0] * 3600 + parts[1] * 60 + parts[2]
                if new_t <= confirmed[-1] or new_t >= duration:
                    print(
                        f"  (timestamp must be after {fmt_time(confirmed[-1])} "
                        f"and before {fmt_time(duration)})"
                    )
                    continue
                confirmed.append(new_t)
                break
            else:
                print("  (enter y, n, or a timestamp like 12:34)")
    return confirmed


def write_chapters_file(breaks, duration, out_path):
    breaks = breaks + [duration]
    lines = [";FFMETADATA1"]
    for i in range(len(breaks) - 1):
        start_ms = int(breaks[i] * 1000)
        end_ms = int(breaks[i + 1] * 1000)
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title=Chapter {i + 1}")
    out_path.write_text("\n".join(lines))


def mux_chapters(input_path, chapters_path, output_path):
    """Convert to AAC/m4b (if needed) and embed the chapter metadata."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-f", "ffmetadata", "-i", str(chapters_path),
        "-map_metadata", "0",
        "-map_chapters", "1",
        "-c:a", "aac", "-b:a", "64k",
        "-map", "0:a",
        str(output_path),
    ]
    out, err, code = run(cmd)
    return err, code


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="raw audio file")
    ap.add_argument("-o", "--output", type=Path, required=True, help="output .m4b path")
    ap.add_argument("--noise-db", type=float, default=-35.0, help="silence threshold in dB (default -35)")
    ap.add_argument("--min-gap", type=float, default=1.5, help="minimum silence length to count as a gap, seconds (default 1.5)")
    ap.add_argument("--min-chapter-len", type=float, default=180.0, help="minimum chapter length, seconds (default 180 = 3 min)")
    args = ap.parse_args()

    check_dependencies()

    if not args.input.exists():
        sys.exit(f"Input file not found: {args.input}")

    print("Reading duration...")
    duration = get_duration(args.input)
    print(f"Duration: {fmt_time(duration)}")

    print("Scanning for silence gaps (this can take a bit for long files)...")
    gaps = detect_silences(args.input, args.noise_db, args.min_gap)
    print(f"Found {len(gaps)} silence gaps.")

    breaks = propose_breaks(gaps, duration, args.min_chapter_len)
    if len(breaks) == 1:
        print("No usable chapter breaks found; output will have a single chapter.")
    else:
        print(f"Proposing {len(breaks) - 1} chapter breaks (chapter 1 always starts at 0:00).")

    confirmed = review_breaks(breaks, duration)
    print(f"\nFinalizing {len(confirmed)} chapters...")

    chapters_txt = args.output.with_suffix(".chapters.txt")
    write_chapters_file(confirmed, duration, chapters_txt)

    print("Muxing chapters into output file...")
    err, code = mux_chapters(args.input, chapters_txt, args.output)

    if code == 0 and args.output.exists():
        print(f"\nDone: {args.output}")
    else:
        print(f"\nSomething went wrong (ffmpeg exit code {code}). ffmpeg output below:")
        print(err)
        sys.exit(1)


if __name__ == "__main__":
    main()
