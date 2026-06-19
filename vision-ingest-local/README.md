# Vision Ingest Local

Interpret and OCR images with a **local vision LLM** — Ollama or LM Studio — over
their OpenAI-compatible APIs. Ask what's in a screenshot, extract text from a
photo, describe a chart, or transcribe a code shot. No cloud, no API keys,
everything stays on your machine and runs on your own GPU.

This tool is **skill + scripts** (no MCP server):

| Component | What it is |
|-----------|------------|
| [`SKILL.md`](SKILL.md) | A Claude skill that tells the agent *when* and *how* to interpret images. |
| [`scripts/`](scripts/) | Three standalone Python scripts the agent (or you) runs directly. |

```
You → Agent → scripts/interpret.py → local server (Ollama / LM Studio) → GPU
              ↑ guided by skill (SKILL.md)
```

---

## Features

- **Local GPU inference** — your own Ollama or LM Studio server, no upload, no keys
- **OCR-first** — defaults to `allenai/olmocr-2-7b` (LM Studio), a model tuned for document text extraction
- **General interpretation too** — describe photos, explain diagrams, read UI screenshots, transcribe code
- **Capability checking** — `check_vision.py` verifies the server is up and the model actually supports vision before you spend time
- **Model discovery** — `discover.py` lists every model on the server and flags which ones are multimodal
- **Auto-downscale** — large images are resized to fit the model's context (preserves aspect ratio)
- **Zero-dependency core** — pure-stdlib HTTP; Pillow only used (optionally) for downscaling

---

## Requirements

- Python 3.10+
- A local vision LLM server, one of:
  - **LM Studio** with a VL model loaded (`lms load allenai/olmocr-2-7b`) — default
  - **Ollama** with a vision model pulled (`ollama pull glm-ocr` / `llava` / `qwen2-vl`)
- (Optional) `pillow` — only needed to downscale oversized images

> **Images only.** For turning **PDFs/DOCX/PPTX** into Markdown, use the sibling
> [`mineru-local`](../mineru-local) tool instead — it's purpose-built for documents.

---

## Installation

### Step 1 — Configure the backend

```bash
cd vision-ingest-local
cp .env.example .env
```

Edit `.env` to match your setup:

| Variable | Description | Default |
|----------|-------------|---------|
| `VISION_BACKEND` | `ollama` or `lmstudio` | `lmstudio` |
| `VISION_SERVER_URL` | Server base URL | `http://localhost:11234` (LM Studio) / `http://localhost:11434` (Ollama) |
| `VISION_MODEL` | Vision-capable model name | `allenai/olmocr-2-7b` (LM Studio) / `glm-ocr:latest` (Ollama) |
| `VISION_DEFAULT_PROMPT` | Prompt used when none is given | `Describe this image in detail.` |
| `VISION_MAX_TOKENS` | Max response length | `1024` |
| `VISION_TEMPERATURE` | 0.0–1.0 (lower = more factual) | `0.1` |

Every setting is also a CLI flag, so `.env` is optional.

### Step 2 — Install the skill

Copy [`SKILL.md`](SKILL.md) into your agent's skills directory (or install via your
skill manager). The skill makes the agent reach for this tool whenever you ask it
to read, OCR, describe, or analyze an image.

---

## Usage

### Via the agent

Just ask naturally:

```
"What's in /home/user/screenshot.png?"
"OCR this receipt and give me just the text"
"Explain what this chart shows"
"Transcribe the code in this screenshot"
```

The skill runs the three-step workflow automatically: verify → interpret.

### Via CLI

```bash
# 1. Verify the server + model (run once per session)
python3 scripts/check_vision.py

# 2. Interpret / OCR an image
python3 scripts/interpret.py --image receipt.png \
  --prompt "Extract all text visible in this image. Return only the text."

# 3. (Optional) See what vision models are available
python3 scripts/discover.py --backend lmstudio
```

**`interpret.py` flags:**

| Flag | Purpose |
|------|---------|
| `--image PATH` | Image to analyze **(required)** |
| `--prompt "…"` | What to ask (default: `VISION_DEFAULT_PROMPT`) |
| `--model NAME` | Override the model from `.env` |
| `--backend ollama\|lmstudio` | Override the backend |
| `--url URL` | Override the server URL |
| `--max-tokens N` | Max response length (default 1024) |
| `--temperature 0.0–1.0` | Lower = more factual (default 0.1) |
| `--max-dim N` | Downscale images whose longest edge exceeds this (default 2048) |
| `--raw` | Print the full JSON response instead of just the text |

### Prompt guidance

The output quality depends heavily on the prompt. Tailor it to the intent:

| You want… | Good prompt |
|-----------|-------------|
| OCR / text extraction | `"Extract all text visible in this image. Return only the text, nothing else."` |
| General description | `"Describe this image in detail."` |
| Chart / diagram | `"Explain what this chart shows — axes, trends, key values."` |
| UI / screenshot | `"Describe every UI element — buttons, fields, labels, and positions."` |
| Code screenshot | `"Transcribe all code visible in this screenshot exactly as written."` |

---

## Scripts

| Script | Purpose |
|--------|---------|
| `check_vision.py` | Confirm the server is reachable, the model exists/loaded, and it reports vision capability. Prints a specific fix on failure. |
| `interpret.py` | Send one image + a prompt to the model; print the text response. |
| `discover.py` | List all models on a backend and flag which support vision. |

---

## Project structure

```
vision-ingest-local/
├── README.md
├── SKILL.md                 # triggers + workflow for the agent
├── .env.example             # config template (copy to .env)
└── scripts/
    ├── check_vision.py      # server + model capability check
    ├── interpret.py         # image → text (OCR / describe / analyze)
    └── discover.py          # list vision-capable models
```

---

## Related

- [olmOCR](https://huggingface.co/allenai/olmOCR-2-7B-1025) — the default OCR model (AllenAI)
- [`mineru-local`](../mineru-local) — sibling tool: local **document** (PDF/DOCX/PPTX) → Markdown
- [Ollama](https://ollama.com) · [LM Studio](https://lmstudio.ai) — the local model servers

---

## License

MIT
