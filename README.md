# AudiobookEngine

`chapter_engine.py` turns raw, chapterless audio into an `.m4b` audiobook
with real chapter markers embedded in the metadata.

**How chapters are detected — two strategies:**

- **Silence-only** (default) — finds pauses in the audio and treats them as
  candidate chapter breaks. Fast and needs no extra install, but on books
  with lots of natural pauses it tends to over-detect. Tune it with
  `--min-chapter-len`, or pass `--target-chapters N` to keep only the most
  pronounced pauses.
- **Whisper** (`--whisper`) — transcribes a short clip after each pause and
  keeps only the ones where the narrator actually announces a chapter
  ("Chapter One", "Chapter 12", "Prologue", …). Much more accurate on
  numbered-chapter audiobooks. Slower, and needs a one-time
  `pip install faster-whisper` (runs locally/offline — no API key, no cost).

**One file or many:**

- **Single file** — everything runs on that one file.
- **Combine mode** — pass multiple audio files (e.g. an audiobook split
  across several disks/tracks) and they're merged into a single `.m4b` in
  the order you list them, then treated as one continuous stream. Chapters
  aren't assumed to line up with file boundaries, so a single disk file can
  contain several chapters (or a chapter can span two disk files).

After detection you're shown the proposed chapter list and asked to confirm
the total count with a single yes/no — no per-chapter prompting. Nothing is
written to disk until you say yes.

## Requirements

- **Python 3**
- **ffmpeg** and **ffprobe**, available on your PATH
- *(optional)* **faster-whisper** — only needed for `--whisper` mode:
  ```
  pip install faster-whisper
  ```
  The first `--whisper` run downloads the chosen model (a few hundred MB);
  after that it works fully offline.

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

### Basic run

```
python chapter_engine.py input.mp3 -o output.m4b
```

After it scans the audio, you're shown the proposed chapter list and asked
once to confirm the total:

```
Proposed 52 chapters:
    1. starts 00:00:00  (00:18:11 long)
    2. starts 00:18:11  (00:15:02 long)
    ...
Proceed with these 52 chapter(s)? [y/n]:
```

Type `y` to write the file, or `n` to abort and re-run with different
options. There's no per-chapter prompting — it's a single sanity check on
the count.

### Whisper mode (recommended for numbered chapters)

If silence detection proposes far more chapters than the book really has,
use `--whisper`. It transcribes a short clip after each pause and keeps only
the ones where the narrator actually says "Chapter One", "Chapter 12",
"Prologue", etc.:

```
python chapter_engine.py input.mp3 -o output.m4b --whisper
```

Requires `pip install faster-whisper` (see Requirements). It's slower than
plain silence detection — it has to transcribe a clip at each candidate
pause — but far more accurate on audiobooks with spoken chapter numbers.

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--noise-db` | `-35` | Silence threshold in dB. Lower (more negative) = quieter to count as silence. |
| `--min-gap` | `1.5` | Minimum length (seconds) of a quiet stretch to count as a gap. |
| `--min-chapter-len` | `180` | Minimum chapter length in seconds (default 3 minutes). Also the minimum spacing between accepted breaks. |
| `--target-chapters` | *(none)* | If you know the real chapter count and aren't using `--whisper`: ranks silence gaps by pause length and keeps the N-1 most pronounced ones, instead of accepting every pause past `--min-chapter-len`. |
| `--whisper` | off | Keep only pauses followed by a spoken chapter announcement (needs faster-whisper). |
| `--whisper-model` | `base` | faster-whisper model size: `tiny`/`base`/`small`/`medium`/`large`. Bigger = more accurate but slower. |
| `--whisper-clip-len` | `10` | Seconds of audio after each pause to transcribe when looking for a chapter cue. |
| `--whisper-lang` | `en` | Language code for transcription. |
| `--title` | *(none)* | Book title to embed as metadata. |
| `--author` | *(none)* | Author to embed as metadata. |

Examples:
```
# silence-only, tuned thresholds
python chapter_engine.py input.mp3 -o output.m4b --noise-db -30 --min-gap 2 --min-chapter-len 300

# you know it has 52 chapters, no whisper
python chapter_engine.py input.mp3 -o output.m4b --target-chapters 52

# most accurate: whisper with a larger model
python chapter_engine.py input.mp3 -o output.m4b --whisper --whisper-model small
```

### Combine mode (multiple files → one book)

Pass the files in playback order — the first one becomes the start of the
book:

```
python chapter_engine.py disk1.mp3 disk2.mp3 disk3.mp3 -o book.m4b --whisper
```

The files are merged into one continuous stream first, then detection (silence
or `--whisper`) runs across the whole thing — all the options above apply
identically. File boundaries themselves aren't treated as chapter breaks.

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
- `--whisper` looks for spoken chapter announcements like "Chapter One" or
  "Prologue". It works best when the narrator actually says the chapter
  number aloud; books with only named/untitled chapters (no "Chapter N"
  cue) won't be picked up and are better handled with `--target-chapters`.
