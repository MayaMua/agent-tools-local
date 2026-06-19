# Media Process Local

Download audio, video, and subtitles from **any yt-dlp-supported site** (YouTube,
Bilibili, and 1000+ others), and transcribe speech to text with a **local Qwen3-ASR
model on GPU**. No cloud transcription API. No per-minute fees. No upload.

This repo bundles three things that work together:

| Component | What it is |
|-----------|------------|
| Package (`src/mcp_media_process_local/`) | Library + MCP server. Callable from Python or CLI. |
| CLI entry points | `mcp-media-process-local`, `media-pipeline`, `media-dl` |
| [`skill/`](skill/) | A Claude/Hermes skill that tells the agent *when* and *how* to use the MCP server. |

```
You → Agent → MCP server (src/mcp_media_process_local/server.py)
                   ├── downloader  (yt-dlp)
                   ├── asr         (Qwen3-ASR, GPU)
                   └── pipeline    (orchestration)
              ↑ guided by skill (skill/)
```

---

## Features

- **Any site** — anything yt-dlp supports; YouTube + Bilibili (direct-API fallback) tested
- **Local GPU transcription** — Qwen3-ASR on CUDA, no cloud, no per-minute cost
- **Subtitles first, ASR fallback** — uses site subtitles when present, transcribes when not
- **Long audio handling** — auto-splits >12 min into 10-minute segments (10s overlap)
- **Authenticated downloads** — optional CookieCloud integration for login/age-gated media
- **Resumable** — already-downloaded artifacts are reused on re-run

---

## Requirements

- Python 3.11–3.13
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- `ffmpeg` + `ffprobe` on PATH
- For ASR: an NVIDIA GPU with CUDA (CPU works but is impractically slow)

---

## Installation

### Step 1 — Install the package

```bash
git clone <this-repo>
cd media-process-local
uv sync
```

### Step 2 — Register the MCP server

```bash
# Claude Code
claude mcp add --transport stdio --scope user media-process-local -- \
  uv run --directory /path/to/media-process-local mcp-media-process-local

# Hermes Agent
hermes config mcp add media-process-local -- \
  uv run --directory /path/to/media-process-local mcp-media-process-local
```

### Step 3 — Install the skill

Copy [`skill/SKILL.md`](skill/SKILL.md) into your agent's skills directory.

### Step 4 — (Optional) Authenticated downloads

Create `.env` in the project root:

```
COOKIECLOUD_URL=https://your-cookiecloud-host
COOKIECLOUD_UUID=your-uuid
COOKIECLOUD_PASSWORD=your-password
```

---

## Usage

### Via the agent (MCP tools)

Once the MCP server is registered, the agent can call:

| Tool | Description |
|------|-------------|
| `check_health` | Verify ffmpeg/ffprobe, yt-dlp, CUDA + Qwen3-ASR, CookieCloud config |
| `download_media` | Download audio / video / site subtitles from a URL (no ASR) |
| `transcribe_audio` | Transcribe a local audio file with Qwen3-ASR (auto-splits long audio) |
| `process_url` | Full pipeline: download audio → subtitles → ASR fallback |
| `unload_asr` | Unload the ASR model and free GPU memory when transcription work is done |

Full parameter documentation: [`skill/references/mcp_tools.md`](skill/references/mcp_tools.md)

### Via CLI

```bash
# Full pipeline (download + transcribe)
uv run media-pipeline links.txt
uv run media-pipeline "https://youtu.be/VIDEO_ID" --video --language zh --markdown

# Download only
uv run media-dl "https://youtu.be/VIDEO_ID" --audio mp3 --transcript --output ./data
uv run media-dl links.txt --audio mp3 --video mp4

# Or using python -m
python -m mcp_media_process_local pipeline "https://youtu.be/xxx"
python -m mcp_media_process_local dl "https://youtu.be/xxx" --audio mp3
```

### Via Python API

```python
from mcp_media_process_local.pipeline import process_url
from mcp_media_process_local.asr import transcribe

# Full pipeline
result = process_url("https://youtu.be/VIDEO_ID", language="zh")
print(result["transcript_preview"])

# Transcribe a local file
text = transcribe("/path/to/audio.mp3", language="en")
```

---

## Output structure

```
<output_dir>/
└── <YYYYMMDD>_<video_title>/
    ├── metadata.json       # title, author, tags, duration, URL
    ├── audio.mp3           # downloaded audio
    └── transcript.txt      # full transcription (UTF-8 text)
```

> **Agent staging convention:** when driven via the skill, the agent stages downloads and
> transcription under `/tmp/media-process` (fast local disk, keeps the workspace clean),
> then copies the finished per-video folder to the destination you specify. See
> [`skill/SKILL.md`](skill/SKILL.md) § Step 2.

---

## Project structure

```
media-process-local/
├── README.md
├── pyproject.toml
├── .env                        # optional CookieCloud credentials
├── .gitignore
├── uv.lock
├── src/mcp_media_process_local/
│   ├── __init__.py             # package metadata
│   ├── __main__.py             # python -m entry point
│   ├── downloader.py           # yt-dlp wrapper (CookieCloud + Bilibili fallback)
│   ├── asr.py                  # Qwen3-ASR local GPU transcription
│   ├── pipeline.py             # Full download + transcribe orchestration
│   └── server.py               # FastMCP server (5 tools)
└── skill/
    ├── SKILL.md                # triggers + workflow for the agent
    └── references/
        └── mcp_tools.md        # full MCP tool parameter reference
```

---

## Related

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — the underlying downloader
- [Qwen3-ASR](https://huggingface.co/Qwen) — the local speech-to-text model

---

## License

MIT
