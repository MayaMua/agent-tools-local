---
name: local-vision
description: "Interpret images using local LLMs (Ollama, LM Studio). Use this skill whenever the user asks what's in an image, wants to interpret/extract/describe/analyze content from a picture, screenshot, photo, diagram, chart, or document. Triggers on phrases like 'interpret this image', 'what's in the image', 'extract content from image', 'describe this picture', 'analyze this screenshot', 'what do you see here', 'read this image', 'OCR this image'. No API key needed — everything runs locally against the user's own models."
metadata:
  openclaw:
    emoji: "👁️"
    requires:
      bins: ["python3"]
---

# Local Vision — Interpret Images with Local LLMs

Send images to a locally running LLM server (Ollama or LM Studio) for
vision-based interpretation using their OpenAI-compatible APIs.
No cloud, no API keys — everything stays on your machine.

## One-time Setup

Copy `.env.example` to `.env` in the skill directory or your project root:

```bash
cp .env.example .env
```

Edit `.env` to match your setup:

| Variable | Description | Default |
|----------|-------------|---------|
| `VISION_BACKEND` | `ollama` or `lmstudio` | `ollama` |
| `VISION_SERVER_URL` | Server base URL | `http://localhost:11434` (ollama) / `http://localhost:1234` (lmstudio) |
| `VISION_MODEL` | Model name | `glm-ocr:latest` |
| `VISION_DEFAULT_PROMPT` | Fallback prompt | `Describe this image in detail.` |
| `VISION_MAX_TOKENS` | Max response length | `1024` |
| `VISION_TEMPERATURE` | 0.0–1.0 (lower = factual) | `0.2` |

The scripts also accept all settings as CLI flags, so `.env` is optional.

## Workflow

When the user asks about an image, follow these three steps:

### Step 1 — Identify the image

Find the image file. It may be:
- A path the user typed (e.g. `/home/user/photo.jpg`)
- An image pasted into the conversation — save it to `/tmp/local-vision-input.png` first
- A file in the current working directory that matches the description

If no image can be found, ask: **"Which image would you like me to analyze?"**

### Step 2 — Verify the server and model

```bash
python3 <skill-path>/scripts/check_vision.py
```

This confirms:
- The server is reachable
- The model exists (and is loaded, for LM Studio)
- The model reports vision capability (Ollama) or has a known vision architecture (LM Studio)

If the check fails, it prints a specific fix. Common issues:

| Result | Fix |
|--------|-----|
| Server unreachable | Ollama: check container / `ollama serve`. LM Studio: `lms server start` |
| Model not found | Ollama: `ollama pull <model>`. LM Studio: `lms load <model>` |
| No vision support | Run `discover.py` to find a vision model, then update `.env` |

### Step 3 — Interpret the image

```bash
python3 <skill-path>/scripts/interpret.py \
  --image <path-to-image> \
  --prompt "What's in this image?"
```

**Common flags:**

| Flag | Purpose |
|------|---------|
| `--image PATH` | Image file to analyze **(required)** |
| `--prompt "…"` | What to ask about the image |
| `--model NAME` | Override the model from `.env` |
| `--backend ollama\|lmstudio` | Override the backend |
| `--url URL` | Override the server URL |
| `--max-tokens N` | Max response length (default 1024) |
| `--temperature 0.0–1.0` | Lower = more factual (default 0.2) |

The script prints the model's response to stdout. Present it directly to the user
as the image interpretation. Use `--raw` if you need the full JSON response.

### Optional — Discover vision models

```bash
python3 <skill-path>/scripts/discover.py --backend ollama
python3 <skill-path>/scripts/discover.py --backend lmstudio
```

Lists all models on the server and flags which ones support vision.
Useful when the user wants to pick a different model or doesn't know what's available.

## Prompt Guidance

The quality of interpretation depends heavily on the prompt. Tailor it to the user's intent:

| User wants… | Good prompt |
|-------------|-------------|
| General description | `"Describe this image in detail."` |
| OCR / text extraction | `"Extract all text visible in this image. Return only the text, nothing else."` |
| Diagram / chart | `"Explain what this chart/diagram shows. Describe axes, trends, and key values."` |
| UI / screenshot | `"Describe every UI element in this screenshot — buttons, fields, labels, and their positions."` |
| Photo analysis | `"Describe this photo — setting, subjects, lighting, composition, mood."` |
| Code screenshot | `"Transcribe all code visible in this screenshot exactly as written."` |
| Specific question | `"<user's exact question>"` |

## Example Session

```
User: "What's in /home/user/screenshot.png?"

Claude:
  → python3 scripts/check_vision.py
    ✓ Server:  http://ollama:11434
    ✓ Model:   glm-ocr:latest
    ✓ Vision:  supported
  → python3 scripts/interpret.py --image /home/user/screenshot.png \
      --prompt "Describe every UI element in this screenshot."
    [model output appears here]
```

## Troubleshooting

| Problem | Likely cause | Solution |
|---------|-------------|----------|
| Connection refused | Server not running | Ollama: `docker ps \| grep ollama`. LM Studio: `lms server start` |
| Model not found | Wrong name or not pulled | Run `discover.py` to see available models |
| 400 Bad Request | Model can't handle images | Run `check_vision.py` — switch to a vision model |
| Empty / garbled output | Prompt mismatch | Try a simpler prompt, or lower temperature to 0.1 |
| Timeout | Image too large | Reduce `--max-dim` (default 2048), or downscale the image first |
| `lms: command not found` | LM Studio not installed | Install from https://lmstudio.ai or use Ollama instead |
