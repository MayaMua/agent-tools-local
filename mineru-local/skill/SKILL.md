---
name: mineru-local
description: "Parse PDFs, Word docs, PPTs, and images into clean Markdown using a locally-installed MinerU MCP server — no API key, no cloud, full GPU acceleration (CUDA or Apple Silicon MLX). Use this skill whenever the user wants to: convert/parse/extract text from a PDF or document, turn a paper into Markdown, OCR a scanned file, extract tables or formulas from a document, batch-convert a folder of files, or save document content to Obsidian. Prefer this over any cloud-based document parser when the user cares about privacy, GPU speed, or offline use."
metadata:
  openclaw:
    emoji: "📄"
    requires:
      bins: ["python3"]
      mcp:
        - name: mineru-local
          description: "MinerU local document parser MCP server"
          install: |
            claude mcp add --transport stdio --scope user mineru-local -- \
              uv run --directory /Workspace/agent-workflow/mineru-local/mcp mcp-mineru-local
---

# MinerU Local — Document Parser

Convert PDF, Word, PPT, XLSX, and images to clean Markdown using a **local MinerU MCP server**.  
No API key. No cloud upload. Full GPU acceleration (CUDA or Apple Silicon MLX).

## One-time Setup

Register the MCP server with Claude (`uv` handles the venv automatically):

```bash
claude mcp add --transport stdio --scope user mineru-local -- \
  uv run --directory /Workspace/agent-workflow/mineru-local/mcp mcp-mineru-local
```

Verify it works:
```bash
claude mcp list   # should show mineru-local
```

For Apple Silicon (MLX support):
```bash
# uv sync with the mlx extra inside the project directory
cd /Workspace/agent-workflow/mineru-local/mcp && uv sync --extra mlx
```

> **Note:** MinerU requires ~2GB of model downloads on first run. Models are cached after that.

## Workflow

Before calling any MCP tool, load the tool schemas with:
```
ToolSearch: select:mcp__mineru-local__check_health,mcp__mineru-local__list_backends,mcp__mineru-local__parse_document,mcp__mineru-local__parse_batch
```
All 4 tools are prefixed `mcp__mineru-local__` in the deferred tool registry.  
**Never use the `mineru` CLI via Bash** — the MCP tools handle output cleanly (no subfolders, no intermediate files).

Every parsing session follows the same three steps:

### Step 1 — Check what GPU you have

```
call: check_health
```

Reports MinerU version, Python version, and whether CUDA or MLX is available.  
Run this once if something isn't working — it pinpoints installation issues immediately.

### Step 2 — Pick the right backend

```
call: list_backends
```

Returns which backends are installed and recommends the best one:

| Backend | Hardware | Speed | Quality |
|---------|----------|-------|---------|
| `pipeline` | CPU — any machine | ~32s/page | Good |
| `vlm-mlx-engine` | Apple Silicon M1–M4 | ~38s/page | Excellent |
| `vlm-transformers` | NVIDIA GPU (CUDA) | ~148s/page | Highest |

Use the `recommended` value from the response as the `backend` parameter below.

### Step 3 — Parse

**Single file:**
```
call: parse_document
  file_path: /absolute/path/to/document.pdf
  backend: <from list_backends>
  save_to: /absolute/path/to/output.md   # always specify — see note below
```

**Whole directory:**
```
call: parse_batch
  directory: /path/to/folder
  backend: <from list_backends>
  pattern: "*.pdf"                        # or "*.{pdf,docx,pptx}"
  output_directory: /path/to/output/      # optional
  skip_existing: true                     # safe resume on interruption
```

> **Always use `save_to` with the exact output path** (e.g. `/output/report.md`).
> MinerU creates backend-named subfolders automatically (e.g. `hybrid_auto/`) when
> it controls the output location. Passing `save_to` bypasses this and writes
> the file exactly where specified — no subfolders.

### Overwrite behaviour

If `save_to` already exists, `parse_document` returns an `output_exists` error
**before** spending any GPU time. When this happens:

1. Inform the user that the file already exists and show the path.
2. Ask whether they want to overwrite it.
3. If yes — delete the old file if needed, then call `parse_document` again
   with `overwrite=True`.
4. If no — suggest a new `save_to` path or skip.

## Supported File Types

| Format | Extensions |
|--------|-----------|
| PDF | `.pdf` |
| Word | `.docx`, `.doc` |
| PowerPoint | `.pptx`, `.ppt` |
| Excel | `.xlsx`, `.xls` |
| Images | `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif` |

## Common Requests → Exact Tool Calls

**"Parse this PDF"** → `parse_document(file_path=..., backend=recommended)`

**"Convert all PDFs in this folder"** → `parse_batch(directory=..., pattern="*.pdf")`

**"Extract tables and formulas from this paper"** → `parse_document(..., enable_formula=True, enable_table=True)`

**"Just parse pages 1–10"** → `parse_document(..., page_range="1-10")`

**"Save to my Obsidian vault"** → `parse_document(..., save_to="~/Obsidian/VaultName/document.md")`

**"OCR this scanned document"** → `parse_document(..., backend="vlm-transformers")` — VLM backends handle scanned docs best

**"Something isn't working"** → `check_health()` — diagnose installation issues

## MCP Tool Reference

Full parameter documentation: [references/mcp_tools.md](references/mcp_tools.md)
