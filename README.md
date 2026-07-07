# AudiobookEngine

`chapter_engine.py` turns raw, chapterless audio into an `.m4b` audiobook
with real chapter markers embedded in the metadata.

Two modes:

- **Single file** — detects silence gaps in one audio file and proposes
  chapter breaks at them, which you review interactively (keep / drop /
  retime each one) before anything is written.
- **Combine mode** — pass multiple audio files (e.g. one per chapter) and
  they're merged into a single `.m4b` in the order you list them, with a
  chapter marker placed at each file boundary.

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
python chapter_engine.py "01 - Intro.mp3" "02 - Chapter One.mp3" "03 - Chapter Two.mp3" -o book.m4b
```

Each input file becomes its own chapter, split at the file boundaries. You'll
be prompted to confirm or rename each chapter's title, which defaults to a
cleaned-up version of the filename:

```
Chapter 1 title [Intro]:
Chapter 2 title [Chapter One]:
```

Press Enter to accept the default, or type a replacement.

Add `-y` / `--yes` to skip the title prompts and just use the filenames as-is:
```
python chapter_engine.py 01.mp3 02.mp3 03.mp3 -o book.m4b -y
```

Works across mixed input formats (e.g. some files mp3, others m4a/wav) —
each is normalized before being joined, so no manual conversion needed.

### Book metadata

Both modes accept `--title` and `--author` to stamp book-level metadata onto
the output:

```
python chapter_engine.py 01.mp3 02.mp3 03.mp3 -o book.m4b --title "My Book" --author "Jane Doe"
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
