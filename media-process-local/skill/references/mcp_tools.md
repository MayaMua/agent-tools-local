# Media Process Local — MCP Tool Reference

Base: local stdio MCP server (`mcp_media_process_local.server`)
No authentication required. CookieCloud credentials (optional) are read from `.env`.

---

## check_health

Verify the toolchain and report system capabilities. Run this to diagnose issues.

**Parameters:** none

**Returns:**

```json
{
  "python": "3.13.12",
  "platform": "Linux-6.17.0-x86_64",
  "architecture": "x86_64",
  "ffmpeg": "/usr/bin/ffmpeg",
  "ffprobe": "/usr/bin/ffprobe",
  "yt_dlp_version": "2026.3.17",
  "yt_dlp_status": "installed",
  "qwen_asr_version": "0.0.6",
  "qwen_asr_status": "installed",
  "cuda_available": true,
  "cuda_device": "NVIDIA RTX A6000",
  "cuda_vram_gb": 50.9,
  "torch_version": "2.9.1+cu128",
  "cookiecloud_configured": true,
  "status": "ready"
}
```

**Common fixes:**
- `ffmpeg: NOT FOUND` → install ffmpeg (`apt install ffmpeg` / `brew install ffmpeg`)
- `yt_dlp_status: NOT INSTALLED` / `qwen_asr_status: NOT INSTALLED` → `uv sync`
- `cuda_available: false` → ASR will be very slow; check `nvidia-smi` and the CUDA torch build
- `cookiecloud_configured: false` → add `COOKIECLOUD_*` to `.env` for login-gated media

---

## download_media

Download audio, video, and/or site-provided subtitles from a URL. Does **not** run ASR.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Media page URL (YouTube, Bilibili, any yt-dlp site) |
| `output_dir` | string | `./output` | Base dir for the per-video output folder |
| `audio` | boolean | `true` | Download audio |
| `video` | boolean | `false` | Also download the video file |
| `transcript` | boolean | `true` | Download site-provided subtitles as `transcript.txt` |
| `audio_format` | string | `"mp3"` | `mp3` \| `m4a` \| `wav` \| … |
| `video_format` | string | `"mp4"` | `mp4` \| `mkv` \| `webm` \| … |

**Returns:**

```json
{
  "output_dir": "./output/20260606_Some Title",
  "title": "Some Title",
  "length_seconds": 612,
  "files": {
    "audio": ".../audio.mp3",
    "metadata": ".../metadata.json",
    "transcript": ".../transcript.txt"
  }
}
```

**Bilibili:** audio only (direct-API fallback); `warnings` lists what was skipped.

---

## transcribe_audio

Transcribe a local audio file to text with Qwen3-ASR (local GPU). Audio longer than
12 minutes is auto-split into 10-minute segments (10s overlap) before transcription.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `audio_path` | string | required | Absolute path to an audio file |
| `output_dir` | string | the file's dir | Where to write `transcript.txt` + segment files |
| `language` | string | `null` | Language hint — ISO code (`"zh"`, `"en"`, `"yue"`, `"ja"`, …) or canonical name (`"Chinese"`, `"English"`, …). Codes are mapped automatically. Omit to auto-detect |
| `overwrite` | boolean | `false` | If `transcript.txt` exists, error unless `true` |

**Returns:**

```json
{
  "transcript": "full transcribed text ...",
  "characters": 8421,
  "segments": 3,
  "duration_min": 24.7,
  "transcript_path": ".../transcript.txt",
  "source": ".../audio.mp3"
}
```

**Overwrite guard:** if `transcript.txt` exists and `overwrite=false`, returns
`{"error": "output_exists", ...}` before loading the model. Confirm with the user,
then retry with `overwrite=true`.

---

## process_url

Full pipeline for one URL: download audio → try site subtitles → fall back to Qwen3-ASR.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | Media page URL |
| `output_dir` | string | `./output` | Base dir for the per-video output folder |
| `video` | boolean | `false` | Also download the video file (mp4) |
| `language` | string | `null` | Language hint for the ASR fallback — ISO code (`"zh"`, `"en"`, …) or canonical name. Omit to auto-detect |
| `markdown` | boolean | `false` | Also write a combined `{title}.md` with metadata + transcript |

**Returns:**

```json
{
  "output_dir": "./output/20260606_Some Title",
  "title": "Some Title",
  "transcript_method": "asr",
  "transcript_chars": 8421,
  "transcript_preview": "first ~500 chars ...",
  "files": {
    "audio": ".../audio.mp3",
    "metadata": ".../metadata.json",
    "transcript": ".../transcript.txt"
  }
}
```

- `transcript_method` is `"subtitles"` (site-provided), `"asr"` (local Qwen3-ASR), or `"none"`.
- If `transcript_method` is `"none"`, check the `warnings` array in the result — it
  explains why (e.g. download failed, or an ASR error).
- Existing artifacts are reused, so re-running resumes an interrupted job cleanly.
- For a list of URLs, call `process_url` once per URL, then `unload_asr` when done.

---

## unload_asr

Unload the Qwen3-ASR model and free its GPU memory. The model stays resident across
calls (so a batch doesn't reload it each time); call this when transcription work is
**finished** — after processing one or more videos/files — to release the GPU.

**Parameters:** none

**Returns:**

```json
{ "unloaded": true, "message": "ASR model unloaded; GPU memory freed." }
```

- Safe to call when nothing is loaded (`"unloaded": false`).
- The next transcription reloads the model automatically (with first-call load time).
- The `media-pipeline` / `media-dl` CLI tools unload automatically at end of a batch;
  this tool is for the long-lived MCP server.
