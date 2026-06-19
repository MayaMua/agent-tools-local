# Agent Tools — Local & GPU-Accelerated

A collection of **local, GPU-accelerated tools for AI agents** (Claude Code,
Hermes, and any MCP/skill-compatible runtime). Each one wraps a heavy local model
— speech-to-text, document parsing, vision/OCR — and exposes it to your agent as
a skill (and, where it helps, an MCP server).

**Why local?** No cloud upload. No API keys. No per-minute or per-page fees. No
rate limits. Your data and your GPU stay yours — which matters for private
documents, clinical data, and anything you'd rather not send to a third party.

---

## What's inside

| Tool | Does | Local model | Interface |
|------|------|-------------|-----------|
| [**media-process-local**](media-process-local/) | Download audio/video/subtitles from YouTube, Bilibili & 1000+ sites, then transcribe speech → text | Qwen3-ASR | MCP server + CLI + skill |
| [**mineru-local**](mineru-local/) | Parse PDF / DOCX / PPTX / images → clean Markdown (LaTeX formulas + tables preserved) | MinerU (VLM) | MCP server + skill |
| [**vision-ingest-local**](vision-ingest-local/) | Interpret & OCR images — describe, extract text, read charts/UI/code | olmOCR-2 / GLM-OCR / LLaVA | scripts + skill |

They compose: scrape a talk with `media-process-local`, OCR its slides with
`vision-ingest-local`, parse the linked paper with `mineru-local`.

---

## The shared pattern

Every tool follows the same two-layer design:

```
You → Agent → tool (MCP server or scripts) → local model → GPU
              ↑ guided by a skill (SKILL.md)
```

- **The skill (`SKILL.md`)** teaches the agent *when* to reach for the tool and
  *how* to drive it — so you can just ask in natural language.
- **The tool** does the work locally: an MCP server for stateful/heavy tools
  (`media-process-local`, `mineru-local`) or plain scripts for lighter ones
  (`vision-ingest-local`).

---

## Requirements

- **Python 3.10–3.13** and [`uv`](https://docs.astral.sh/uv/) (manages each tool's environment)
- **An NVIDIA GPU with CUDA** is strongly recommended (Apple Silicon MLX supported by `mineru-local`). CPU works for some tools but is slow.
- A **local model server** for the vision tool — [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai)
- Tool-specific extras: `ffmpeg` (media), see each tool's README

Each tool installs and runs independently — pick the ones you need.

---

## Quick start

Clone, then follow the README in whichever tool you want:

```bash
git clone <this-repo-url> agent-tools-local
cd agent-tools-local

# then, e.g.:
cd mineru-local        && cat README.md   # PDF/DOCX → Markdown
cd media-process-local && cat README.md   # download + transcribe
cd vision-ingest-local && cat README.md   # image OCR / interpretation
```

Most tools register with Claude Code in one line, e.g.:

```bash
claude mcp add --transport stdio --scope user mineru-local -- \
  uv run --directory "$(pwd)/mineru-local/mcp" mcp-mineru-local
```

---

## Repository layout

```
agent-tools-local/
├── README.md                 # you are here
├── .gitignore                # excludes .env (secrets), .venv, model weights, media
├── media-process-local/      # yt-dlp + Qwen3-ASR  (MCP + CLI + skill)
├── mineru-local/             # MinerU document → Markdown  (MCP + skill)
└── vision-ingest-local/      # local vision LLM image OCR / interpretation  (scripts + skill)
```

---

## A note on configuration & secrets

Tools read configuration from a per-tool `.env` (copy the provided `.env.example`).
**Real `.env` files are git-ignored** — they may hold credentials (e.g. CookieCloud
for authenticated downloads) or local server URLs. Never commit one; commit the
`.env.example` template instead.

---

## License

MIT — see individual tools for any upstream model/library licenses.
