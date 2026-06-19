# MinerU Local — MCP Tool Reference

Base: local stdio MCP server (`mcp_mineru_local.server`)  
No authentication required.

---

## check_health

Verify MinerU installation and hardware. Run this to diagnose issues.

**Parameters:** none

**Returns:**

```json
{
  "python": "3.12.0",
  "platform": "macOS-14.0-arm64",
  "architecture": "arm64",
  "mineru_version": "3.0.1",
  "mineru_status": "installed",
  "cuda_available": false,
  "mlx_available": true,
  "mlx_version": "0.16.0",
  "status": "ready"
}
```

**Common fixes:**
- `mineru_status: NOT INSTALLED` → `pip install 'mineru[core]>=3.0.0'`
- `mlx_available: false` on Apple Silicon → `pip install 'mineru[mlx]>=3.0.0'`
- `cuda_available: false` on Linux with GPU → check `nvidia-smi`, install CUDA-compatible PyTorch

---

## list_backends

Detect available backends and get a recommendation for this machine.

**Parameters:** none

**Returns:**

```json
{
  "recommended": "vlm-mlx-engine",
  "reason": "MLX VLM — optimized for M1-M4, great quality with low power draw",
  "backends": {
    "pipeline": {
      "available": true,
      "hardware": "CPU",
      "speed": "~32s/page",
      "quality": "good",
      "description": "CPU pipeline backend — fast, works everywhere"
    },
    "vlm-mlx-engine": {
      "available": true,
      "hardware": "Apple Silicon (arm, 16.0GB unified memory)",
      "speed": "~38s/page",
      "quality": "excellent",
      "description": "MLX VLM — optimized for M1-M4"
    }
  }
}
```

---

## parse_document

Parse a single document into Markdown.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | required | Absolute path to the document |
| `backend` | string | `"pipeline"` | `pipeline` \| `vlm-mlx-engine` \| `vlm-transformers` |
| `enable_formula` | boolean | `true` | Convert math to LaTeX |
| `enable_table` | boolean | `true` | Preserve table structure |
| `page_range` | string | `null` | e.g. `"1-5"` or `"1,3,7-10"` |
| `save_to` | string | `null` | Absolute path to write `.md` file |

**Returns:**

```json
{
  "markdown": "# Document Title\n\n...",
  "characters": 12450,
  "backend_used": "vlm-mlx-engine",
  "source": "/path/to/document.pdf",
  "saved_to": "/path/to/output.md"
}
```

**Error hints:**
- `FileNotFoundError` — check the path; the error message suggests nearby filenames
- `Unsupported file type` — see supported extensions in SKILL.md
- `MinerU CLI failed` — run `check_health` to diagnose

---

## parse_batch

Parse all matching documents in a directory sequentially.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `directory` | string | required | Path to folder |
| `backend` | string | `"pipeline"` | Same as parse_document |
| `pattern` | string | `"*.pdf"` | Glob pattern, e.g. `"*.{pdf,docx}"` |
| `enable_formula` | boolean | `true` | Convert math to LaTeX |
| `enable_table` | boolean | `true` | Preserve table structure |
| `output_directory` | string | `null` | Defaults to `<directory>/mineru-output/` |
| `skip_existing` | boolean | `true` | Skip files with existing `.md` output |

**Returns:**

```json
{
  "total": 10,
  "processed": 8,
  "skipped": 1,
  "failed": 1,
  "output_directory": "/path/to/mineru-output",
  "files": [
    {"file": "paper.pdf", "status": "done", "saved_to": "...", "characters": 8200},
    {"file": "report.pdf", "status": "skipped (already exists)"},
    {"file": "corrupt.pdf", "status": "failed", "error": "MinerU CLI failed..."}
  ]
}
```

**Resume interrupted batches:** re-run with `skip_existing: true` — already-parsed files are skipped automatically.
