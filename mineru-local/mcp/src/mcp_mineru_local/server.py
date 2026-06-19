#!/usr/bin/env python3
"""
MinerU Local MCP Server

Provides document parsing (PDF, DOCX, PPTX, images) via locally-installed MinerU,
with automatic GPU backend selection: MLX for Apple Silicon, CUDA for NVIDIA, CPU pipeline fallback.

Install:
    pip install -e .

Register with Claude Code:
    claude mcp add --transport stdio --scope user mineru-local -- \
        python -m mcp_mineru_local.server
"""

import asyncio
import importlib.metadata
import platform
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

mcp = FastMCP(
    name="mineru-local",
    instructions=(
        "Parse documents into Markdown using local MinerU with GPU acceleration. "
        "Workflow: (1) call list_backends to pick the best backend for this machine, "
        "(2) call parse_document for a single file or parse_batch for a directory. "
        "MinerU supports PDF, DOCX, PPTX, XLSX, and images (JPG/PNG/WebP)."
    ),
)

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".jpg", ".jpeg", ".png", ".webp", ".gif",
}

BACKEND_PRIORITY = ["hybrid-auto", "vlm-mlx-engine", "vlm-transformers", "pipeline"]


_api_server = None
_api_server_lock = asyncio.Lock()



def _normalize_path(path: str) -> Path:
    """Resolve path with macOS Unicode normalization and helpful error on missing file."""
    normalized = unicodedata.normalize("NFKC", path)
    p = Path(normalized).expanduser().resolve()
    if not p.exists():
        parent = p.parent
        hint = ""
        if parent.exists():
            candidates = sorted(
                (f for f in parent.iterdir() if f.is_file()),
                key=lambda f: -sum(a == b for a, b in zip(f.name, p.name)),
            )
            if candidates:
                hint = f"\nDid you mean: {candidates[0]}"
                if len(candidates) > 1:
                    hint += f"\nNearby files: {[f.name for f in candidates[1:4]]}"
        raise FileNotFoundError(f"File not found: {path}{hint}")
    return p


def _detect_backends() -> dict[str, dict]:
    """Probe installed packages to report which MinerU backends are usable."""
    result: dict[str, dict] = {}

    # Pipeline (CPU) — available if mineru is installed
    try:
        ver = importlib.metadata.version("mineru")
        result["pipeline"] = {
            "available": True,
            "hardware": "CPU",
            "speed": "~32s/page",
            "quality": "good",
            "description": f"CPU pipeline backend — fast, works everywhere (MinerU {ver})",
        }
    except importlib.metadata.PackageNotFoundError:
        result["pipeline"] = {
            "available": False,
            "hardware": "CPU",
            "description": "MinerU not installed — run: pip install 'mineru[core]>=3.0.0'",
        }
        return result  # nothing else will work either

    # MLX — Apple Silicon
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx  # noqa: F401
            import mlx.core as mx  # noqa: F401
            mem_gb = round(mx.metal.device_info()["memory_size"] / 1e9, 1)
            result["vlm-mlx-engine"] = {
                "available": True,
                "hardware": f"Apple Silicon ({platform.processor()}, {mem_gb}GB unified memory)",
                "speed": "~38s/page",
                "quality": "excellent",
                "description": "MLX VLM — optimized for M1-M4, great quality with low power draw",
            }
        except ImportError:
            result["vlm-mlx-engine"] = {
                "available": False,
                "hardware": "Apple Silicon",
                "description": "MLX not installed — run: pip install 'mineru[mlx]>=3.0.0'",
            }

    # CUDA — NVIDIA GPU
    try:
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
            vram = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
            result["vlm-transformers"] = {
                "available": True,
                "hardware": f"CUDA — {gpu} ({vram}GB VRAM)",
                "speed": "~148s/page",
                "quality": "highest",
                "description": "Transformers VLM — highest quality, best for complex layouts",
            }
        else:
            result["vlm-transformers"] = {
                "available": False,
                "hardware": "CUDA",
                "description": "PyTorch installed but no CUDA GPU detected",
            }
    except ImportError:
        pass  # torch not installed — skip silently

    # Hybrid Auto
    is_hybrid_available = (
        result.get("vlm-mlx-engine", {}).get("available") or 
        result.get("vlm-transformers", {}).get("available")
    )
    if is_hybrid_available:
        result["hybrid-auto"] = {
            "available": True,
            "hardware": result.get("vlm-mlx-engine", {}).get("hardware") or result.get("vlm-transformers", {}).get("hardware"),
            "speed": "~20s/page",
            "quality": "highest",
            "description": "Hybrid VLM + Layout Pipeline — next-generation high-accuracy solution, faster than pure VLM",
        }
    else:
        result["hybrid-auto"] = {
            "available": False,
            "hardware": "CUDA or Apple Silicon",
            "description": "Hybrid auto requires a CUDA GPU or Apple Silicon system",
        }

    return result


async def _get_api_server():
    global _api_server
    async with _api_server_lock:
        if _api_server is None:
            from mineru.cli.api_client import LocalAPIServer, wait_for_local_api_ready
            import httpx

            server = LocalAPIServer()
            server.start()

            timeout = httpx.Timeout(connect=10, read=60, write=300, pool=30)
            async with httpx.AsyncClient(timeout=timeout) as http_client:
                await wait_for_local_api_ready(http_client, server)
            _api_server = server
        return _api_server


async def _parse_via_persistent_api(
    file_path: Path,
    output_dir: Path,
    backend: str,
    enable_formula: bool,
    enable_table: bool,
    page_range: Optional[str],
) -> str:
    """Parse document by calling the persistent local FastAPI server."""
    server = await _get_api_server()
    base_url = server.base_url

    # Map backend to cli-compatible backend
    cli_backend_map = {
        "pipeline": "pipeline",
        "vlm-transformers": "vlm-auto-engine",
        "vlm-mlx-engine": "vlm-auto-engine",
        "hybrid-auto": "hybrid-auto-engine",
    }
    cli_backend = cli_backend_map.get(backend, "hybrid-auto-engine")

    # Resolve page range
    start_page_id = 0
    end_page_id = None
    if page_range:
        parts = page_range.split("-")
        if len(parts) == 2:
            start_page_id = int(parts[0]) - 1
            end_page_id = int(parts[1]) - 1

    from mineru.cli.client import (
        build_request_form_data,
        submit_task,
        wait_for_task_result,
        download_result_zip,
        safe_extract_zip,
        InputDocument,
        PlannedTask,
    )
    import httpx

    form_data = build_request_form_data(
        lang="ch",
        backend=cli_backend,
        method="auto",
        formula_enable=enable_formula,
        table_enable=enable_table,
        server_url=None,
        start_page_id=start_page_id,
        end_page_id=end_page_id,
    )

    # Estimate page count
    effective_pages = 1
    if file_path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium
            from mineru.utils.pdfium_guard import (
                open_pdfium_document,
                get_pdfium_document_page_count,
                close_pdfium_document,
            )
            pdf_doc = open_pdfium_document(pdfium.PdfDocument, str(file_path))
            try:
                page_count = get_pdfium_document_page_count(pdf_doc)
                effective_pages = page_count
            finally:
                close_pdfium_document(pdf_doc)
        except Exception:
            pass

    doc = InputDocument(
        path=file_path,
        suffix=file_path.suffix,
        stem=file_path.stem,
        effective_pages=effective_pages,
        order=0,
    )
    planned_task = PlannedTask(
        index=1,
        documents=[doc],
        total_pages=effective_pages,
    )

    timeout = httpx.Timeout(connect=10, read=60, write=300, pool=30)
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        submit_resp = await submit_task(
            client=http_client,
            base_url=base_url,
            planned_task=planned_task,
            form_data=form_data,
        )

        await wait_for_task_result(
            client=http_client,
            submit_response=submit_resp,
            planned_task=planned_task,
        )

        zip_path = await download_result_zip(
            client=http_client,
            submit_response=submit_resp,
            planned_task=planned_task,
        )
        try:
            safe_extract_zip(zip_path, output_dir)
        finally:
            zip_path.unlink(missing_ok=True)

    md_files = sorted(output_dir.rglob("*.md"))
    if md_files:
        return md_files[0].read_text(encoding="utf-8")
    return ""


async def _run_mineru(
    file_path: Path,
    output_dir: Path,
    backend: str,
    enable_formula: bool,
    enable_table: bool,
    page_range: Optional[str],
) -> str:
    """
    Invoke MinerU parser and return the resulting Markdown.

    Tries the persistent local API server first (much faster, no subprocess startup overhead).
    Falls back to the Python library API or CLI subprocess if the API server fails.
    """
    # --- Attempt 1: Persistent API Server (preferred) ---
    try:
        markdown = await _parse_via_persistent_api(
            file_path, output_dir, backend, enable_formula, enable_table, page_range
        )
        if markdown:
            return markdown
    except Exception:
        pass  # Fall through on any failure to be robust

    # --- Attempt 2: Python API (MinerU >= 3.0) ---
    try:
        markdown = await _parse_via_python_api(
            file_path, output_dir, backend, enable_formula, enable_table, page_range
        )
        if markdown:
            return markdown
    except (ImportError, AttributeError, TypeError):
        pass  # API shape changed — fall through to CLI

    # --- Attempt 3: mineru CLI subprocess ---
    return await _parse_via_cli(
        file_path, output_dir, backend, enable_formula, enable_table, page_range
    )


async def _parse_via_python_api(
    file_path: Path,
    output_dir: Path,
    backend: str,
    enable_formula: bool,
    enable_table: bool,
    page_range: Optional[str],
) -> str:
    """MinerU >= 3.0 Python library path."""
    # MinerU 3.x exposes different surfaces depending on the minor version.
    # We try the most common patterns and let the caller fall back to CLI.
    try:
        # Pattern A: high-level async parse function (preferred, v3.1+)
        from mineru.api import parse_document as _parse  # type: ignore

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: _parse(
                str(file_path),
                output_dir=str(output_dir),
                backend=backend,
                enable_formula=enable_formula,
                enable_table=enable_table,
                page_range=page_range,
            ),
        )
    except ImportError:
        # Pattern B: UNIPipe (v3.0)
        from mineru.pipe.uni_pipe import UNIPipe  # type: ignore
        from mineru.data.data_reader_writer import FileBasedDataWriter  # type: ignore

        image_writer = FileBasedDataWriter(str(output_dir / "images"))
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        loop = asyncio.get_event_loop()
        pipe = await loop.run_in_executor(
            None,
            lambda: UNIPipe(
                pdf_bytes=pdf_bytes,
                jso_useful_key={"_pdf_type": "", "model_list": []},
                image_writer=image_writer,
            ),
        )
        await loop.run_in_executor(None, pipe.pipe_classify)
        await loop.run_in_executor(None, pipe.pipe_parse)
        md_content = await loop.run_in_executor(None, pipe.pipe_mk_markdown)
        if md_content:
            return md_content if isinstance(md_content, str) else "\n".join(md_content)

    # Read back from output directory
    md_files = sorted(output_dir.rglob("*.md"))
    if md_files:
        return md_files[0].read_text(encoding="utf-8")
    return ""


async def _parse_via_cli(
    file_path: Path,
    output_dir: Path,
    backend: str,
    enable_formula: bool,
    enable_table: bool,
    page_range: Optional[str],
) -> str:
    """Fallback: invoke the `mineru` CLI as a subprocess."""
    import shutil

    # Locate the mineru binary (prefer venv-local, fall back to PATH)
    mineru_bin = shutil.which("mineru") or str(Path(sys.executable).parent / "mineru")

    # Map our internal backend names to mineru CLI -b values
    cli_backend_map = {
        "pipeline": "pipeline",
        "vlm-transformers": "vlm-auto-engine",   # local CUDA/transformers
        "vlm-mlx-engine": "vlm-auto-engine",      # local MLX (macOS)
        "hybrid-auto": "hybrid-auto-engine",
    }
    cli_backend = cli_backend_map.get(backend, "hybrid-auto-engine")

    cmd = [
        mineru_bin,
        "-p", str(file_path),
        "-o", str(output_dir),
        "-b", cli_backend,
        "-f", str(enable_formula).lower(),
        "-t", str(enable_table).lower(),
    ]
    if page_range:
        # page_range like "1-5" → -s 0 -e 4 (0-indexed)
        parts = page_range.split("-")
        if len(parts) == 2:
            cmd += ["-s", str(int(parts[0]) - 1), "-e", str(int(parts[1]) - 1)]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:800]
        raise RuntimeError(
            f"MinerU CLI failed (exit {proc.returncode}).\n\n"
            f"Command: {' '.join(str(c) for c in cmd)}\n\n"
            f"Error output:\n{err}\n\n"
            "Tip: run `check_health` to verify your MinerU installation."
        )

    md_files = sorted(output_dir.rglob("*.md"))
    if md_files:
        return md_files[0].read_text(encoding="utf-8")
    return stdout.decode(errors="replace")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
def list_backends() -> dict:
    """
    Detect available MinerU backends and recommend the best one for this machine.

    Always call this before parse_document so you pick the right backend.
    Returns availability, hardware details, speed estimate, and a clear recommendation.
    """
    backends = _detect_backends()

    recommended = next(
        (b for b in BACKEND_PRIORITY if backends.get(b, {}).get("available")),
        "pipeline",
    )

    return {
        "recommended": recommended,
        "reason": backends.get(recommended, {}).get("description", ""),
        "backends": backends,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def check_health() -> dict:
    """
    Verify MinerU is correctly installed and report system capabilities.

    Run this first to diagnose installation issues before trying to parse documents.
    Reports MinerU version, Python version, platform, CUDA/MLX availability.
    """
    info: dict = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }

    # MinerU
    try:
        info["mineru_version"] = importlib.metadata.version("mineru")
        info["mineru_status"] = "installed"
    except importlib.metadata.PackageNotFoundError:
        info["mineru_status"] = "NOT INSTALLED"
        info["fix"] = "pip install 'mineru[core]>=3.0.0'"
        return info

    # CUDA
    try:
        import torch
        info["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            info["cuda_device"] = torch.cuda.get_device_name(0)
            info["cuda_vram_gb"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1e9, 1
            )
        info["torch_version"] = torch.__version__
    except ImportError:
        info["cuda_available"] = False

    # MLX
    if platform.machine() == "arm64" and platform.system() == "Darwin":
        try:
            import mlx.core as mx  # noqa: F401
            info["mlx_available"] = True
            info["mlx_version"] = importlib.metadata.version("mlx")
        except (ImportError, importlib.metadata.PackageNotFoundError):
            info["mlx_available"] = False
            info["mlx_fix"] = "pip install 'mineru[mlx]>=3.0.0'"

    info["status"] = "ready"
    return info


@mcp.tool()
async def parse_document(
    file_path: str,
    backend: str = "pipeline",
    enable_formula: bool = True,
    enable_table: bool = True,
    page_range: Optional[str] = None,
    save_to: Optional[str] = None,
    overwrite: bool = False,
) -> dict:
    """
    Parse a document (PDF, DOCX, PPTX, XLSX, JPG, PNG, WebP) into clean Markdown.

    Call list_backends first to pick the right backend:
    - 'pipeline'         → CPU, always works, ~32s/page
    - 'vlm-mlx-engine'   → Apple Silicon M1-M4, ~38s/page, excellent quality
    - 'vlm-transformers' → NVIDIA CUDA GPU, ~148s/page, highest quality

    IMPORTANT: Always pass save_to as a full file path (e.g. /output/doc.md).
    This writes the result directly to that path with no subfolders. If you let
    MinerU choose the output location it will create backend-named subfolders
    (e.g. hybrid_auto/) that the user does not want.

    Args:
        file_path:     Absolute path to the document.
        backend:       Which MinerU backend to use (see above). Default: 'pipeline'.
        enable_formula: Detect and convert math formulas to LaTeX. Default: True.
        enable_table:  Preserve table structure in Markdown. Default: True.
        page_range:    Restrict parsing to specific pages, e.g. '1-5' or '1,3,7-10'.
                       Omit to parse all pages.
        save_to:       Full path for the output .md file (e.g. /output/report.md).
                       Strongly recommended — avoids MinerU's automatic subfolder layout.
                       If omitted, Markdown is returned in the response only.
        overwrite:     If save_to already exists and overwrite=False (default), the tool
                       returns an error asking for confirmation. Set overwrite=True to
                       replace the existing file after the user confirms.

    Returns:
        markdown     — The extracted Markdown content.
        characters   — Character count (useful for gauging output size).
        backend_used — Which backend was actually used.
        saved_to     — Path of the saved file (only if save_to was provided).
    """
    path = _normalize_path(file_path)

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{path.suffix}'.\n"
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # Overwrite guard — check before spending GPU time
    if save_to:
        out = Path(save_to).expanduser().resolve()
        if out.exists() and not overwrite:
            return {
                "error": "output_exists",
                "message": (
                    f"Output file already exists: {out}\n"
                    "Ask the user whether they want to overwrite it, then call "
                    "parse_document again with overwrite=True to replace it."
                ),
                "existing_file": str(out),
            }

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        markdown = await _run_mineru(
            path, output_dir, backend, enable_formula, enable_table, page_range
        )

        result: dict = {
            "markdown": markdown,
            "characters": len(markdown),
            "backend_used": backend,
            "source": str(path),
        }

        if save_to:
            out = Path(save_to).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            
            import shutil
            md_files = sorted(output_dir.rglob("*.md"))
            source_dir = md_files[0].parent if md_files else output_dir
            
            for item in source_dir.iterdir():
                if item.suffix.lower() == ".md":
                    shutil.copy2(item, out)
                else:
                    dest = out.parent / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
            result["saved_to"] = str(out)

    return result


@mcp.tool()
async def parse_batch(
    directory: str,
    backend: str = "pipeline",
    pattern: str = "*.pdf",
    enable_formula: bool = True,
    enable_table: bool = True,
    output_directory: Optional[str] = None,
    skip_existing: bool = True,
) -> dict:
    """
    Parse every matching document in a directory, one at a time.

    Processes files sequentially so GPU memory is never exhausted.
    With skip_existing=True, you can safely interrupt and resume large batches.

    Args:
        directory:        Path to folder containing documents.
        backend:          MinerU backend — same options as parse_document.
        pattern:          Glob pattern to filter files. Default: '*.pdf'.
                          Use '*.{pdf,docx}' for multiple types.
        enable_formula:   Extract LaTeX formulas. Default: True.
        enable_table:     Preserve table structure. Default: True.
        output_directory: Where to write .md files.
                          Defaults to <directory>/mineru-output/.
        skip_existing:    Skip files that already have a .md output.
                          Set to False to re-parse everything. Default: True.

    Returns:
        Summary counts (total/processed/skipped/failed) and per-file status.
        All Markdown files are written to output_directory.
    """
    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    out_dir = (
        Path(output_directory).expanduser() if output_directory
        else dir_path / "mineru-output"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(dir_path.glob(pattern))
    if not files:
        return {
            "message": f"No files matching '{pattern}' found in {directory}",
            "total": 0,
        }

    summary: dict = {
        "total": len(files),
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "output_directory": str(out_dir),
        "files": [],
    }

    for file_path in files:
        out_file = out_dir / f"{file_path.stem}.md"

        if skip_existing and out_file.exists():
            summary["skipped"] += 1
            summary["files"].append({"file": file_path.name, "status": "skipped (already exists)"})
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            summary["files"].append({"file": file_path.name, "status": "skipped (unsupported type)"})
            continue

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                output_dir = Path(tmpdir)
                markdown = await _run_mineru(
                    file_path, output_dir, backend, enable_formula, enable_table, None
                )
                
                import shutil
                md_files = sorted(output_dir.rglob("*.md"))
                source_dir = md_files[0].parent if md_files else output_dir
                
                for item in source_dir.iterdir():
                    if item.suffix.lower() == ".md":
                        shutil.copy2(item, out_file)
                    else:
                        dest = out_dir / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)
            summary["processed"] += 1
            summary["files"].append({
                "file": file_path.name,
                "status": "done",
                "saved_to": str(out_file),
                "characters": len(markdown),
            })
        except Exception as exc:
            summary["failed"] += 1
            summary["files"].append({
                "file": file_path.name,
                "status": "failed",
                "error": str(exc)[:300],
            })

    return summary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
