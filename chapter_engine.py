#!/usr/bin/env python3
"""
chapter_engine.py — v1

Takes raw, chapterless audio file(s) (mp3/wav/m4a/etc.) and produces an
.m4b with real chapter markers. Nothing is written until you confirm.

Two modes:

  Single file — silence detection proposes chapter breaks, which you
  review interactively (keep / drop / retime each one) before anything
  is finalized:
      python chapter_engine.py input.mp3 -o output.m4b
      python chapter_engine.py input.mp3 -o output.m4b --min-gap 1.5 --min-chapter-len 180

  Multiple files — combine mode. Files are concatenated in the order
  given on the command line (first file = start of the book), with a
  chapter marker placed at each file boundary. Chapters are titled
  "Chapter 1", "Chapter 2", etc. automatically:
      python chapter_engine.py 01.mp3 02.mp3 03.mp3 -o output.m4b
      python chapter_engine.py 01.mp3 02.mp3 03.mp3 -o output.m4b --title "My Book" --author "Jane Doe"

Requires: ffmpeg + ffprobe on your PATH (brew install ffmpeg)

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


def escape_ffmetadata(value):
    """Escape characters that are special in the ffmetadata1 format."""
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def write_chapters_file(breaks, duration, out_path, titles=None, global_tags=None):
    breaks = breaks + [duration]
    lines = [";FFMETADATA1"]
    if global_tags:
        for key, value in global_tags.items():
            if value:
                lines.append(f"{key}={escape_ffmetadata(value)}")
    for i in range(len(breaks) - 1):
        start_ms = int(breaks[i] * 1000)
        end_ms = int(breaks[i + 1] * 1000)
        title = titles[i] if titles else f"Chapter {i + 1}"
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={escape_ffmetadata(title)}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def mux_chapters(input_path, chapters_path, output_path, has_global_tags):
    """Convert to AAC/m4b (if needed) and embed the chapter metadata."""
    metadata_source = "1" if has_global_tags else "0"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-f", "ffmetadata", "-i", str(chapters_path),
        "-map_metadata", metadata_source,
        "-map_chapters", "1",
        "-c:a", "aac", "-b:a", "64k",
        "-map", "0:a",
        str(output_path),
    ]
    _, err, code = run(cmd)
    return err, code


def build_concat_filter(n):
    """Normalize each input's audio format, then concatenate them in order."""
    branches = []
    labels = []
    for i in range(n):
        branches.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a{i}]"
        )
        labels.append(f"[a{i}]")
    concat = "".join(labels) + f"concat=n={n}:v=0:a=1[outa]"
    return ";".join(branches + [concat])


def combine_and_mux(input_paths, chapters_path, output_path, has_global_tags):
    """Concatenate multiple audio files in order and embed chapter metadata."""
    cmd = ["ffmpeg", "-y"]
    for p in input_paths:
        cmd += ["-i", str(p)]
    chapters_index = len(input_paths)
    cmd += ["-f", "ffmetadata", "-i", str(chapters_path)]
    cmd += [
        "-filter_complex", build_concat_filter(len(input_paths)),
        "-map", "[outa]",
        "-map_metadata", str(chapters_index) if has_global_tags else "-1",
        "-map_chapters", str(chapters_index),
        "-c:a", "aac", "-b:a", "64k",
        str(output_path),
    ]
    _, err, code = run(cmd)
    return err, code


def finalize(err, code, output_path):
    if code == 0 and output_path.exists():
        print(f"\nDone: {output_path}")
    else:
        print(f"\nSomething went wrong (ffmpeg exit code {code}). ffmpeg output below:")
        print(err)
        sys.exit(1)


def run_single_file(args):
    input_path = args.inputs[0]

    print("Reading duration...")
    duration = get_duration(input_path)
    print(f"Duration: {fmt_time(duration)}")

    print("Scanning for silence gaps (this can take a bit for long files)...")
    gaps = detect_silences(input_path, args.noise_db, args.min_gap)
    print(f"Found {len(gaps)} silence gaps.")

    breaks = propose_breaks(gaps, duration, args.min_chapter_len)
    if len(breaks) == 1:
        print("No usable chapter breaks found; output will have a single chapter.")
    else:
        print(f"Proposing {len(breaks) - 1} chapter breaks (chapter 1 always starts at 0:00).")

    confirmed = review_breaks(breaks, duration)
    print(f"\nFinalizing {len(confirmed)} chapters...")

    global_tags = {"title": args.title, "artist": args.author}
    has_global_tags = bool(args.title or args.author)

    chapters_txt = args.output.with_suffix(".chapters.txt")
    write_chapters_file(confirmed, duration, chapters_txt, global_tags=global_tags)

    print("Muxing chapters into output file...")
    err, code = mux_chapters(input_path, chapters_txt, args.output, has_global_tags)
    finalize(err, code, args.output)


def run_combine(args):
    print(f"Combine mode: {len(args.inputs)} files will be merged in this order:")
    for i, p in enumerate(args.inputs, start=1):
        print(f"  {i}. {p}")

    print("\nReading durations...")
    durations = [get_duration(p) for p in args.inputs]
    total_duration = sum(durations)

    breaks = [0.0]
    for d in durations[:-1]:
        breaks.append(breaks[-1] + d)

    global_tags = {"title": args.title, "artist": args.author}
    has_global_tags = bool(args.title or args.author)

    chapters_txt = args.output.with_suffix(".chapters.txt")
    write_chapters_file(breaks, total_duration, chapters_txt, global_tags=global_tags)

    print("\nCombining files and muxing chapters into output file...")
    err, code = combine_and_mux(args.inputs, chapters_txt, args.output, has_global_tags)
    finalize(err, code, args.output)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "inputs", type=Path, nargs="+",
        help="raw audio file(s). Pass one file for silence-detected chapters, "
             "or multiple files (in playback order) to combine them with a "
             "chapter per file"
    )
    ap.add_argument("-o", "--output", type=Path, required=True, help="output .m4b path")
    ap.add_argument("--noise-db", type=float, default=-35.0, help="silence threshold in dB, single-file mode only (default -35)")
    ap.add_argument("--min-gap", type=float, default=1.5, help="minimum silence length to count as a gap, seconds, single-file mode only (default 1.5)")
    ap.add_argument("--min-chapter-len", type=float, default=180.0, help="minimum chapter length, seconds, single-file mode only (default 180 = 3 min)")
    ap.add_argument("--title", help="book title to embed as metadata")
    ap.add_argument("--author", help="author name to embed as metadata")
    args = ap.parse_args()

    check_dependencies()

    missing = [str(p) for p in args.inputs if not p.exists()]
    if missing:
        sys.exit(f"Input file(s) not found: {', '.join(missing)}")

    if len(args.inputs) == 1:
        run_single_file(args)
    else:
        run_combine(args)


if __name__ == "__main__":
    main()
