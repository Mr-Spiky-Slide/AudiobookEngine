#!/usr/bin/env python3
"""
chapter_engine.py — v2

Takes raw, chapterless audio file(s) (mp3/wav/m4a/etc.) and produces an
.m4b with real chapter markers. You confirm the chapter count before
anything is written.

Detecting chapters — two strategies:

  Silence-only (default, no extra install): finds pauses in the audio
  and treats them as candidate chapter breaks. Fast, but on books with
  frequent natural pauses it over-detects. Tune with --min-chapter-len,
  or set --target-chapters N to keep only the N-1 most pronounced pauses.

  Whisper (--whisper, needs `pip install faster-whisper`): listens to a
  short clip after each pause and keeps only the ones where the narrator
  actually announces a chapter ("Chapter One", "Chapter 12", "Prologue",
  ...). Much more accurate on numbered-chapter audiobooks, but slower and
  requires the extra package (runs locally/offline — no API key, no cost).

Handling one file vs. many:

  Single file — everything runs on that file:
      python chapter_engine.py input.mp3 -o output.m4b
      python chapter_engine.py input.mp3 -o output.m4b --whisper

  Multiple files — combine mode. Files are concatenated in the order
  given on the command line (first file = start of the book, e.g. disk
  1, disk 2, ...), then treated as one continuous stream — chapters
  aren't assumed to line up with file boundaries:
      python chapter_engine.py disk1.mp3 disk2.mp3 disk3.mp3 -o output.m4b --whisper
      python chapter_engine.py disk1.mp3 disk2.mp3 disk3.mp3 -o output.m4b --title "My Book" --author "Jane Doe"

Chapters are titled "Chapter 1", "Chapter 2", etc. automatically.

Requires: ffmpeg + ffprobe on your PATH (brew install ffmpeg).
Optional:  faster-whisper (`pip install faster-whisper`) for --whisper.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
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


def parse_silence_log(err):
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", err)]

    # pair them up (ffmpeg logs start then end in order). If the stream ends
    # while still silent, the trailing silence_start has no matching end —
    # zip() drops it, which is fine since we don't want a break right at EOF.
    return list(zip(starts, ends))


def detect_silences(path, noise_db, min_silence_len):
    """Run ffmpeg's silencedetect filter on one file and parse start/end timestamps."""
    _, err, code = run([
        "ffmpeg", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_len}",
        "-f", "null", "-"
    ])
    if code != 0:
        sys.exit(f"ffmpeg silence detection failed on {path}:\n{err.strip()}")
    return parse_silence_log(err)


def detect_silences_multi(paths, noise_db, min_silence_len):
    """Concatenate multiple files in memory and run silencedetect across the
    whole merged stream, so chapter breaks aren't limited to file boundaries
    (e.g. audiobooks split across several disk/track files)."""
    cmd = ["ffmpeg"]
    for p in paths:
        cmd += ["-i", str(p)]
    filter_complex = (
        f"{build_concat_filter(len(paths))};"
        f"[outa]silencedetect=noise={noise_db}dB:d={min_silence_len}[sout]"
    )
    cmd += ["-filter_complex", filter_complex, "-map", "[sout]", "-f", "null", "-"]
    _, err, code = run(cmd)
    if code != 0:
        sys.exit(f"ffmpeg silence detection failed across input files:\n{err.strip()}")
    return parse_silence_log(err)


def propose_breaks(gaps, duration, min_chapter_len, target_chapters=None):
    """Turn silence gaps into candidate chapter-break timestamps.

    Default behavior: walk the gaps in order and keep the midpoint of any
    gap that's at least min_chapter_len away from the last accepted break.
    This treats every sufficiently-spaced pause as a chapter break, which
    over-detects on books with frequent natural pauses (paragraph breaks,
    dramatic beats) shorter than real chapter breaks.

    If target_chapters is given, instead rank gaps by how long the silence
    itself is (a real chapter break is usually a more pronounced pause than
    a mid-narration breath) and greedily keep the target_chapters - 1
    longest ones that are still at least min_chapter_len apart, then sort
    them back into chronological order.
    """
    if target_chapters:
        needed = max(0, target_chapters - 1)
        ranked = sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)
        selected = []
        for s, e in ranked:
            if len(selected) >= needed:
                break
            mid = (s + e) / 2
            if mid < min_chapter_len or duration - mid < min_chapter_len:
                continue
            if all(abs(mid - b) >= min_chapter_len for b in selected):
                selected.append(mid)
        return [0.0] + sorted(selected)

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


def confirm_breaks(breaks, duration):
    """Show the proposed chapters and ask for a single yes/no on the total,
    rather than confirming each break one at a time."""
    n = len(breaks)
    if n == 1:
        print("\nNo chapter breaks found — the output would be a single chapter.")
    else:
        print(f"\nProposed {n} chapters:")
        ends = breaks[1:] + [duration]
        preview = list(range(n)) if n <= 60 else list(range(30)) + [None] + list(range(n - 20, n))
        for idx in preview:
            if idx is None:
                print(f"      ... {n - 50} more ...")
                continue
            start = breaks[idx]
            length = ends[idx] - start
            print(f"  {idx + 1:>3}. starts {fmt_time(start)}  ({fmt_time(length)} long)")
    while True:
        resp = input(f"\nProceed with these {n} chapter(s)? [y/n]: ").strip().lower()
        if resp == "y":
            return breaks
        if resp == "n":
            sys.exit(
                "Aborted — nothing was written. Re-run with different options "
                "(e.g. --whisper, --target-chapters N, or a larger --min-chapter-len)."
            )
        print("  (enter y or n)")


# --- Whisper-based chapter-cue detection -------------------------------------

_NUM_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)"
)

# Matches a spoken chapter announcement: "Chapter 1", "Chapter Twenty-Three",
# "Prologue", etc. Not anchored to the start of the string — see
# find_chapter_cue, which searches only the first few words of a clip so
# a short preamble ("Book One.", "This audiobook is narrated by...") before
# the actual announcement doesn't cause a miss, without matching a stray
# mention of "chapter" anywhere in a longer clip.
CHAPTER_CUE = re.compile(
    r"\b(?:"
    r"chapter\s+(?:\d+|" + _NUM_WORD + r"(?:[\s-]+(?:and\s+)?" + _NUM_WORD + r")*)"
    r"|prologue|epilogue|introduction|foreword|afterword|preface|interlude"
    r")\b",
    re.IGNORECASE,
)

# How many leading characters of a clip's transcript count as "the start" for
# cue-matching purposes. Wide enough to skip a short preamble phrase, narrow
# enough that an unrelated "chapter" mention deeper in a longer clip won't
# falsely trigger a chapter break.
CUE_SEARCH_WINDOW = 100


def clean_cue_text(text):
    """Normalize a transcribed clip before cue-matching: Whisper often prepends
    quotes, dashes, ellipses or a stray '[music]'-style tag. Strip that leading
    noise so it doesn't eat into the cue-search window."""
    text = text.strip()
    text = re.sub(r"^\s*\[[^\]]*\]\s*", "", text)  # drop a leading [sound] tag
    text = re.sub(r"^[^0-9A-Za-z]+", "", text)     # drop leading quotes/dashes/dots
    return text


def find_chapter_cue(text):
    """Look for a chapter announcement near the start of a transcribed clip
    (within the first CUE_SEARCH_WINDOW characters), tolerating a short
    preamble before it."""
    return CHAPTER_CUE.search(clean_cue_text(text)[:CUE_SEARCH_WINDOW])


def map_abs_to_file(paths, durations, t):
    """Map an absolute timestamp in the merged stream to (path, local_offset)."""
    acc = 0.0
    for p, d in zip(paths, durations):
        if t < acc + d:
            return p, t - acc
        acc += d
    return paths[-1], durations[-1]


def extract_clip(path, start, length, out_wav):
    """Pull a short mono 16kHz wav clip out of `path` for transcription."""
    start = max(0.0, start)
    _, err, code = run([
        "ffmpeg", "-y", "-ss", str(start), "-i", str(path),
        "-t", str(length), "-ac", "1", "-ar", "16000",
        "-f", "wav", str(out_wav),
    ])
    if code != 0:
        sys.exit(f"ffmpeg failed to extract clip at {fmt_time(start)} from {path}:\n{err.strip()}")


def detect_chapters_via_transcript(gaps, paths, durations, duration, args):
    """Keep only the silence gaps that are immediately followed by a spoken
    chapter announcement, verified by transcribing a short clip after each
    pause with Whisper. Returns break timestamps (silence midpoints)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit(
            "Whisper mode needs the faster-whisper package. Install it with:\n"
            "    pip install faster-whisper\n"
            "(runs locally/offline — no API key or account needed)."
        )

    print(f"Loading Whisper model '{args.whisper_model}' (first run downloads it)...")
    model = WhisperModel(args.whisper_model, device="cpu", compute_type="int8")

    print(f"Listening to {len(gaps)} pauses for spoken chapter announcements "
          "(this is the slow part)...")
    breaks = [0.0]
    tmpdir = Path(tempfile.mkdtemp(prefix="chapeng_"))
    try:
        clip = tmpdir / "clip.wav"
        for i, (s, e) in enumerate(gaps, 1):
            mid = (s + e) / 2
            # A chapter can't begin in the first/last second of the book.
            if mid < 1.0 or duration - mid < 1.0:
                continue
            # In Whisper mode the spoken cue is the source of truth, so we do
            # NOT gate on chapter length — that would skip short chapters. The
            # only guard is a small de-dupe window so a single announcement
            # caught across two adjacent micro-pauses isn't counted twice.
            if len(breaks) > 1 and mid - breaks[-1] < args.whisper_dedupe:
                continue
            # Map the clip to whichever file speech resumes in (so a chapter
            # announced right after a disk boundary lands in the right file),
            # then back up 0.3s within that file so the first word isn't clipped.
            path, offset = map_abs_to_file(paths, durations, e)
            extract_clip(path, offset - 0.3, args.whisper_clip_len, clip)
            segments, _ = model.transcribe(str(clip), language=args.whisper_lang, beam_size=1)
            text = " ".join(seg.text for seg in segments).strip()
            if find_chapter_cue(text):
                breaks.append(mid)
                print(f"  [{fmt_time(mid)}] chapter cue: \"{text[:50].strip()}\"")
            if i % 50 == 0:
                print(f"  ...checked {i}/{len(gaps)} pauses, {len(breaks) - 1} chapters so far")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return breaks


def choose_breaks(gaps, duration, paths, durations, args):
    """Pick chapter breaks using the strategy selected on the command line."""
    if args.whisper:
        if args.target_chapters:
            print(
                "Note: --target-chapters is ignored when --whisper is set "
                "(Whisper decides breaks from spoken chapter cues instead)."
            )
        return detect_chapters_via_transcript(gaps, paths, durations, duration, args)
    if args.target_chapters:
        print(f"Ranking gaps by pause length to find the {args.target_chapters} most likely chapter breaks...")
    return propose_breaks(gaps, duration, args.min_chapter_len, args.target_chapters)


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

    breaks = choose_breaks(gaps, duration, [input_path], [duration], args)
    confirmed = confirm_breaks(breaks, duration)
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
    print(f"Total duration: {fmt_time(total_duration)}")

    print("Scanning for silence gaps across all files (this can take a bit for long books)...")
    gaps = detect_silences_multi(args.inputs, args.noise_db, args.min_gap)
    print(f"Found {len(gaps)} silence gaps.")

    breaks = choose_breaks(gaps, total_duration, args.inputs, durations, args)
    confirmed = confirm_breaks(breaks, total_duration)
    print(f"\nFinalizing {len(confirmed)} chapters...")

    global_tags = {"title": args.title, "artist": args.author}
    has_global_tags = bool(args.title or args.author)

    chapters_txt = args.output.with_suffix(".chapters.txt")
    write_chapters_file(confirmed, total_duration, chapters_txt, global_tags=global_tags)

    print("Combining files and muxing chapters into output file...")
    err, code = combine_and_mux(args.inputs, chapters_txt, args.output, has_global_tags)
    finalize(err, code, args.output)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "inputs", type=Path, nargs="+",
        help="raw audio file(s). Pass one file, or multiple files in "
             "playback order (e.g. one per disk/track) to be merged and "
             "scanned as a single continuous stream"
    )
    ap.add_argument("-o", "--output", type=Path, required=True, help="output .m4b path")
    ap.add_argument("--noise-db", type=float, default=-35.0, help="silence threshold in dB (default -35)")
    ap.add_argument("--min-gap", type=float, default=1.5, help="minimum silence length to count as a gap, seconds (default 1.5)")
    ap.add_argument("--min-chapter-len", type=float, default=180.0, help="minimum chapter length, seconds (default 180 = 3 min); also used as minimum spacing between breaks when --target-chapters is set")
    ap.add_argument("--target-chapters", type=int, help="if you know the expected chapter count, rank silence gaps by pause length and keep the N-1 most pronounced ones instead of accepting every gap past --min-chapter-len")
    ap.add_argument("--whisper", action="store_true", help="use Whisper to keep only pauses followed by a spoken chapter announcement (needs `pip install faster-whisper`; slower but far more accurate)")
    ap.add_argument("--whisper-model", default="base", help="faster-whisper model size: tiny/base/small/medium/large (default base; bigger = more accurate but slower)")
    ap.add_argument("--whisper-clip-len", type=float, default=10.0, help="seconds of audio after each pause to transcribe when looking for a chapter cue (default 10)")
    ap.add_argument("--whisper-dedupe", type=float, default=20.0, help="minimum seconds between two accepted chapter cues, to avoid counting one announcement twice; does NOT skip short chapters the way --min-chapter-len would (default 20)")
    ap.add_argument("--whisper-lang", default="en", help="language code for transcription (default en)")
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
