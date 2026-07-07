# AudiobookEngine

`chapter_engine.py` turns raw, chapterless audio into an `.m4b` audiobook
with real chapter markers embedded in the metadata.

Two modes:

- **Single file** — detects silence gaps in one audio file and proposes
  chapter breaks at them, which you review interactively (keep / drop /
  retime each one) before anything is written.
- **Combine mode** — pass multiple audio files (e.g. an audiobook split
  across several disks/tracks) and they're merged into a single `.m4b` in
  the order you list them. Silence detection then runs across the *whole*
  merged stream — chapters aren't assumed to line up with file boundaries,
  so a single disk file can still contain several chapters (or a chapter
  can span two disk files). Same interactive review as single-file mode.

Nothing is written to disk until you confirm the chapters.

## Requirements

- **Python 3** (no extra packages — standard library only)
- **ffmpeg** and **ffprobe**, available on your PATH

### Installing ffmpeg

**Windows**
```powershell
winget install ffmpeg
```
(or `choco install ffmpeg`, or download a build from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to
your PATH)

**macOS**
```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**
```bash
sudo apt install ffmpeg
```

Verify both tools are found:
```
ffmpeg -version
ffprobe -version
```

## Setup

1. Download `chapter_engine.py` into a folder.
2. Put your audio file(s) in the same folder (or reference them by path).
3. Open a terminal in that folder:
   - Windows: hold **Shift**, right-click empty space in File Explorer →
     "Open PowerShell window here" (or "Open Terminal here")
   - macOS/Linux: `cd` into the folder

If your files are in the same folder as the script and you open your
terminal there, you can just use plain filenames — no full path required.
Wrap any filename that contains spaces in quotes.

## Usage

### Single file (silence-detected chapters)

```
python chapter_engine.py input.mp3 -o output.m4b
```

You'll be walked through each proposed chapter break:

```
Proposed chapter break at 00:14:32 (chapter would be 00:14:32 long) — keep? [y/n/type new mm:ss]:
```

Type `y` to keep it, `n` to drop it, or a timestamp like `12:34` to move it.

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--noise-db` | `-35` | Silence threshold in dB. Lower (more negative) = quieter to count as silence. |
| `--min-gap` | `1.5` | Minimum length (seconds) of a quiet stretch to count as a gap. |
| `--min-chapter-len` | `180` | Minimum chapter length in seconds (default 3 minutes). |

Example with tuned thresholds:
```
python chapter_engine.py input.mp3 -o output.m4b --noise-db -30 --min-gap 2 --min-chapter-len 300
```

### Combine mode (multiple files → one book)

Pass the files in playback order — the first one becomes the start of the
book:

```
python chapter_engine.py disk1.mp3 disk2.mp3 disk3.mp3 -o book.m4b
```

The files are merged into one continuous stream first, then scanned for
silence just like single-file mode — you get the same proposed-break review
prompts, and the same `--noise-db` / `--min-gap` / `--min-chapter-len`
options apply. Chapters are titled "Chapter 1", "Chapter 2", etc.
automatically. File boundaries themselves aren't treated as chapter breaks —
only actual silence gaps are.

Works across mixed input formats (e.g. some files mp3, others m4a/wav) —
each is normalized before being joined, so no manual conversion needed.

### Book metadata

Both modes accept `--title` and `--author` to stamp book-level metadata onto
the output:

```
python chapter_engine.py disk1.mp3 disk2.mp3 disk3.mp3 -o book.m4b --title "My Book" --author "Jane Doe"
```

In single-file mode, if you don't pass `--title`/`--author`, the original
file's existing metadata (title, artist, album, etc.) is preserved.

## Output

The script writes two files:

- `<output>.m4b` — the final audiobook with embedded chapters
- `<output>.chapters.txt` — the intermediate ffmetadata file used to embed
  the chapters (kept alongside the output; safe to delete afterward)

## Notes

- Audio is always re-encoded to AAC at 64kbps. This is generally fine for
  spoken word, but if you're starting from a high-bitrate source and care
  about preserving quality, be aware the output is re-encoded rather than
  passed through.
- Chapter detection quality depends on the source audio, not its format —
  mp3, m4a, wav, etc. are all decoded the same way before analysis.
