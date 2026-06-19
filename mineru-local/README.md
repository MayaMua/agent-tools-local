# MinerU Local

Parse PDFs, Word docs, PPTs, and images into clean Markdown — locally, with full GPU acceleration. No API key. No cloud upload. No rate limits.

This repo bundles two components that work together:

| Component | What it is |
|-----------|------------|
| [`mcp/`](mcp/) | A FastMCP server that wraps MinerU. Claude calls it as a tool. |
| [`skill/`](skill/) | A Claude skill that tells Claude *when* and *how* to use the MCP server. |

```
You → Claude → MCP server (mcp/) → MinerU → GPU (CUDA or MLX)
               ↑ guided by skill (skill/)
```

---

## Features

- **Local GPU acceleration** — CUDA on NVIDIA, MLX on Apple Silicon (M1–M4), CPU fallback
- **All MinerU formats** — PDF, DOCX, PPTX, XLSX, JPG, PNG, WebP
- **LaTeX formulas + tables** — preserved in the Markdown output
- **Batch processing** — convert entire directories with resume support
- **No setup friction** — `uv` manages the Python environment automatically

---

## Requirements

- Python 3.10–3.13
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- MinerU-compatible GPU (optional but recommended):
  - NVIDIA: CUDA 12.1+, 8GB+ VRAM
  - Apple Silicon: M1 or newer

---

## Installation

### Step 1 — Register the MCP server

This single command registers the server with Claude Code. `uv` handles the Python environment automatically on first run (downloads MinerU and its dependencies, ~2GB).

```bash
claude mcp add --transport stdio --scope user mineru-local -- \
  uv run --directory /Workspace/agent-workflow/mineru-local/mcp mcp-mineru-local
```

Confirm it registered:

```bash
claude mcp list
# mineru-local   stdio   uv run ...
```

### Step 2 — Install the skill

Copy [`skill/SKILL.md`](skill/SKILL.md) into your Claude skills directory, or install via your skill manager.

With OpenClaw / ClawHub, point the installer at this repo's `skill/` folder.

### Step 3 — (Apple Silicon only) Enable MLX

```bash
cd /Workspace/agent-workflow/mineru-local/mcp
uv sync --extra mlx
```

### Step 4 — First run

MinerU downloads model weights (~2GB) the first time it parses a document. Subsequent runs are fast.

---

## Usage

Once installed, just ask Claude naturally:

```
"Parse this PDF into Markdown"
"Convert all papers in ~/research/papers/ and save to ~/research/markdown/"
"Extract the tables and formulas from paper.pdf, save to notes.md"
"Something's wrong with MinerU, can you check?"
```

Claude follows the skill's workflow automatically:

1. `check_health` — verifies MinerU is installed; surfaces issues immediately
2. `list_backends` — detects your GPU and picks the right backend
3. `parse_document` or `parse_batch` — runs the conversion

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `check_health` | Verify MinerU installation, Python version, CUDA/MLX availability |
| `list_backends` | Detect available backends and get a hardware-specific recommendation |
| `parse_document` | Parse a single file into Markdown; optionally save to a path |
| `parse_batch` | Parse all matching files in a directory; supports resume via `skip_existing` |

### Backend reference

| Backend | Hardware | Speed | Quality |
|---------|----------|-------|---------|
| `pipeline` | CPU — any machine | ~32s/page | Good |
| `vlm-mlx-engine` | Apple Silicon M1–M4 | ~38s/page | Excellent |
| `vlm-transformers` | NVIDIA CUDA GPU | ~148s/page | Highest |

`list_backends` always recommends the best backend for your machine.

Full parameter documentation: [`skill/references/mcp_tools.md`](skill/references/mcp_tools.md)

---

## Project structure

```
mineru-local/
├── README.md
├── mcp/                        # MCP server
│   ├── pyproject.toml
│   └── src/mcp_mineru_local/
│       └── server.py           # 4 tools: check_health, list_backends,
│                               #          parse_document, parse_batch
└── skill/                      # Claude skill
    ├── SKILL.md                # Triggers + workflow instructions for Claude
    └── references/
        └── mcp_tools.md        # Full parameter reference
```

---

## Related

- [MinerU](https://github.com/opendatalab/MinerU) — the underlying document parser
- [MinerU-Skill](https://github.com/Nebutra/MinerU-Skill) — cloud API version of this skill (requires API token, no local GPU needed)
- [mcp-mineru](https://github.com/TINKPA/mcp-mineru) — original MCP server this was inspired by

---

## License

MIT
