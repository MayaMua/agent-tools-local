---
name: media-process-local
description: "Download audio/video/subtitles from YouTube, Bilibili, and any other yt-dlp-supported site, and transcribe speech to text locally with Qwen3-ASR on GPU — no cloud transcription API, no per-minute fees. Use this skill whenever the user wants to: download a video or its audio, grab a YouTube/Bilibili transcript or subtitles, transcribe an audio/video file or a recording, turn a talk/podcast/lecture into text, get the transcript of a link, or batch-process a list of media URLs. Prefer this over any cloud transcription service when the user cares about privacy, GPU speed, or offline use."
metadata:
  openclaw:
    emoji: "🎬"
    requires:
      bins: ["python3", "ffmpeg", "ffprobe"]
      mcp:
        - name: media-process-local
          description: "Local media downloader + Qwen3-ASR transcription MCP server"
          install: |
            claude mcp add --transport stdio --scope user media-process-local -- \
              uv run --directory /Workspace/agent-workflow/git-to-share/agent-tools-local/media-process-local mcp-media-process-local
---

# Media Process Local — Download & Transcribe

Download audio, video, and subtitles from **any yt-dlp-supported site** (YouTube,
Bilibili, and 1000+ others), and transcribe speech to text with a **local Qwen3-ASR
model on GPU**. No cloud transcription API. No per-minute fees. No upload.

## One-time Setup

Register the MCP server with Claude (`uv` handles the venv automatically):

```bash
claude mcp add --transport stdio --scope user media-process-local -- \
  uv run --directory /Workspace/agent-workflow/git-to-share/agent-tools-local/media-process-local mcp-media-process-local
```

Verify it works:
```bash
claude mcp list   # should show media-process-local
```

> **Requirements:** `ffmpeg` + `ffprobe` on PATH, and (for ASR) an NVIDIA GPU with
> CUDA. Qwen3-ASR downloads model weights on the first transcription of a session.

> **Authenticated downloads (optional):** create a `.env` in the project root with
> `COOKIECLOUD_URL`, `COOKIECLOUD_UUID`, `COOKIECLOUD_PASSWORD` to pull login/age-gated
> media via CookieCloud. `check_health` reports whether it is configured.

## Workflow

Before calling any MCP tool, load the tool schemas with:
```
ToolSearch: select:mcp__media-process-local__check_health,mcp__media-process-local__download_media,mcp__media-process-local__transcribe_audio,mcp__media-process-local__process_url,mcp__media-process-local__unload_asr
```
All 5 tools are prefixed `mcp__media-process-local__`.
**Prefer these MCP tools over the CLI scripts** — the MCP
tools return structured results (paths, char counts, method used) instead of console text.
The CLI entry points (`media-pipeline`, `media-dl`) are available for direct terminal use.

### Step 1 — Check the toolchain

```
call: check_health
```

Reports ffmpeg/ffprobe, yt-dlp version, CUDA GPU + Qwen3-ASR availability, and whether
CookieCloud is configured. Run this first if anything misbehaves.

### Step 2 — Run the pipeline

For most requests ("get me the transcript of this video"), use the all-in-one tool:

```
call: process_url
  url: https://www.youtube.com/watch?v=...
  output_dir: ./output   # optional; this is the default
  video: false                        # set true to also keep the .mp4
```

`process_url` does the full chain: **download audio → try site subtitles → fall back to
local Qwen3-ASR** (auto-splitting audio longer than 12 min into 10-min segments). It
reuses already-downloaded artifacts, so re-running resumes cleanly. The response reports
`transcript_method` (`subtitles` / `asr` / `none`), `transcript_chars`, and a preview.

### Individual steps

**Download only (no ASR):**
```
call: download_media
  url: https://...
  audio: true          # default
  video: true          # also keep the video file
  transcript: true     # grab site-provided subtitles if any
```

**Transcribe a local file you already have:**
```
call: transcribe_audio
  audio_path: /absolute/path/to/audio.mp3
  language: zh          # optional hint; omit for auto-detect
```

> **Language hint:** pass an ISO code (`zh`, `en`, `yue`, `ja`, …) **or** the
> canonical name (`Chinese`, `English`, `Cantonese`, …) — codes are mapped
> automatically. Omit to auto-detect. For a known-language talk, passing the hint
> improves accuracy.

### Step 3 — Free the GPU when the job is done

The Qwen3-ASR model stays resident in GPU memory after the first transcription so a
batch of videos doesn't reload it each time. **When transcription work is finished —
after processing one or more videos/files — call `unload_asr` to release the GPU:**

```
call: unload_asr
```

Do this once you've handed the user their transcript(s) and have no more pending
media to process. It's safe to call even if nothing was loaded, and the next
transcription reloads the model automatically.

> The `media-pipeline` / `media-dl` CLI entry points unload automatically when the
> batch finishes — this rule is for the long-lived MCP server, which keeps the model
> loaded across calls.

### Overwrite behaviour

`transcribe_audio` returns an `output_exists` error (before spending GPU time) if
`transcript.txt` already exists. When this happens: tell the user, ask whether to
overwrite, and if yes call again with `overwrite=True`.

## Bilibili note

Bilibili 412s yt-dlp's metadata path, so the server uses a **direct-API fallback** that
downloads audio only (worst-quality stream, no VIP needed). Bilibili video and
site-subtitle downloads are not supported — transcripts come from ASR.

## Common Requests → Exact Tool Calls

**"Get the transcript of this video"** → `process_url(url=...)`

**"Download the audio of this YouTube video"** → `download_media(url=..., audio=True, transcript=False)`

**"Download the video file too"** → `process_url(url=..., video=True)` or `download_media(url=..., video=True)`

**"Transcribe this mp3 / recording"** → `transcribe_audio(audio_path=...)`

**"Transcribe this Chinese talk"** → `transcribe_audio(audio_path=..., language="zh")`

**"Process all these links"** → call `process_url` once per URL (read the list, loop), then `unload_asr` when finished

**"I'm done" / finished transcribing** → `unload_asr()` — free the GPU

**"Something isn't working"** → `check_health()` — diagnose ffmpeg / GPU / yt-dlp issues

## MCP Tool Reference

Full parameter documentation: [references/mcp_tools.md](references/mcp_tools.md)
