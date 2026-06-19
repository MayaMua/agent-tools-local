#!/usr/bin/env python3
"""
Media Process Local MCP Server

Download audio/video/subtitles from any yt-dlp-supported site and transcribe
speech to text locally with Qwen3-ASR on GPU.

Register:
    hermes config mcp add media-process-local -- \\
        uv run --directory /path/to/media-process-local mcp-media-process-local

This is a thin FastMCP wrapper around the package modules (downloader, asr, pipeline).
"""

import importlib.metadata
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from .downloader import (
    bilibili_download_audio,
    download_audio,
    download_transcript,
    download_video,
    fetch_info,
    is_bilibili_url,
    make_output_dir,
    save_metadata,
)
from .pipeline import transcribe_to_file

DEFAULT_OUTPUT = Path("./output")

mcp = FastMCP(
    name="media-process-local",
    instructions=(
        "Download and transcribe online media locally. "
        "Workflow: (1) call check_health to confirm ffmpeg / yt-dlp / Qwen3-ASR "
        "are ready, (2) call process_url for the full pipeline (download audio + "
        "get a transcript), or use download_media / transcribe_audio for individual "
        "steps. Supports YouTube, Bilibili (direct-API fallback), and any other "
        "yt-dlp site. Transcription runs on a local GPU via Qwen3-ASR — no cloud API."
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_output(output_dir: Optional[str]) -> Path:
    base = Path(output_dir).expanduser() if output_dir else DEFAULT_OUTPUT
    base.mkdir(parents=True, exist_ok=True)
    return base


def _list_outputs(out_dir: Path) -> dict[str, str]:
    """Report standard artifacts present in an output directory."""
    files = {}
    for key, name in (
        ("audio", "audio.mp3"),
        ("metadata", "metadata.json"),
        ("transcript", "transcript.txt"),
    ):
        p = out_dir / name
        if p.exists():
            files[key] = str(p)
    videos = sorted(out_dir.glob("video.*"))
    if videos:
        files["video"] = str(videos[0])
    md_files = sorted(out_dir.glob("*.md"))
    if md_files:
        files["markdown"] = str(md_files[0])
    return files


def _bilibili_out_dir(base: Path, url: str) -> Path:
    m = re.search(r"BV[a-zA-Z0-9]+", url)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_dir = base / f"{ts}_bilibili_{m.group(0) if m else 'unknown'}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# ═══════════════════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool(annotations={"readOnlyHint": True})
def check_health() -> dict:
    """
    Verify the media-processing toolchain and report system capabilities.

    Run this first to diagnose issues before downloading or transcribing.
    Reports ffmpeg/ffprobe, yt-dlp version, CUDA/torch + Qwen3-ASR availability,
    and whether CookieCloud (for authenticated downloads) is configured.
    """
    info: dict = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }

    info["ffmpeg"] = shutil.which("ffmpeg") or "NOT FOUND"
    info["ffprobe"] = shutil.which("ffprobe") or "NOT FOUND"

    try:
        info["yt_dlp_version"] = importlib.metadata.version("yt-dlp")
        info["yt_dlp_status"] = "installed"
    except importlib.metadata.PackageNotFoundError:
        info["yt_dlp_status"] = "NOT INSTALLED"
        info["yt_dlp_fix"] = "uv sync (adds yt-dlp[default])"

    try:
        info["qwen_asr_version"] = importlib.metadata.version("qwen-asr")
        info["qwen_asr_status"] = "installed"
    except importlib.metadata.PackageNotFoundError:
        info["qwen_asr_status"] = "NOT INSTALLED"
        info["qwen_asr_fix"] = "uv sync (adds qwen-asr[vllm])"

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
        info["torch_status"] = "NOT INSTALLED"

    # CookieCloud — report config presence, never echo credential values
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass
    info["cookiecloud_configured"] = all(
        os.environ.get(k)
        for k in ("COOKIECLOUD_URL", "COOKIECLOUD_UUID", "COOKIECLOUD_PASSWORD")
    )

    ready = (
        info["ffmpeg"] != "NOT FOUND"
        and info.get("yt_dlp_status") == "installed"
    )
    info["status"] = "ready" if ready else "needs setup"
    return info


@mcp.tool()
def download_media(
    url: str,
    output_dir: Optional[str] = None,
    audio: bool = True,
    video: bool = False,
    transcript: bool = True,
    audio_format: str = "mp3",
    video_format: str = "mp4",
) -> dict:
    """
    Download audio, video, and/or site-provided subtitles from a media URL.

    This does NOT run ASR — it only fetches what the site offers. For sites
    that publish no subtitles, set transcript=True here to grab any that exist,
    then call transcribe_audio (or just use process_url for the full pipeline).

    Bilibili is handled via a direct-API fallback (yt-dlp HTTP 412 workaround)
    and currently supports audio only.
    """
    base = _resolve_output(output_dir)

    if is_bilibili_url(url):
        out_dir = _bilibili_out_dir(base, url)
        result: dict = {
            "output_dir": str(out_dir), "site": "bilibili", "warnings": []
        }
        if audio:
            bilibili_download_audio(url, out_dir, audio_format)
        if video:
            result["warnings"].append(
                "Bilibili video download not supported — audio only."
            )
        if transcript:
            result["warnings"].append(
                "Bilibili subtitle download not supported."
            )
        result["files"] = _list_outputs(out_dir)
        return result

    info = fetch_info(url)
    out_dir = make_output_dir(base, info)
    save_metadata(out_dir, info)

    if audio:
        download_audio(url, out_dir, audio_format)
    if video:
        download_video(url, out_dir, video_format)
    if transcript:
        download_transcript(url, out_dir)

    return {
        "output_dir": str(out_dir),
        "title": info.get("title"),
        "length_seconds": info.get("duration"),
        "files": _list_outputs(out_dir),
    }


@mcp.tool()
def transcribe_audio(
    audio_path: str,
    output_dir: Optional[str] = None,
    language: Optional[str] = None,
    overwrite: bool = False,
) -> dict:
    """
    Transcribe a local audio file to text with Qwen3-ASR (local GPU).

    Audio longer than 12 minutes is automatically split into 10-minute segments
    (10s overlap) before transcription. The result is written as transcript.txt.

    Loading the ASR model takes time and GPU memory on the first call of a session.
    """
    path = Path(audio_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    out_dir = (
        Path(output_dir).expanduser().resolve() if output_dir else path.parent
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = out_dir / "transcript.txt"
    if transcript_path.exists() and not overwrite:
        return {
            "error": "output_exists",
            "message": (
                f"Transcript already exists: {transcript_path}\n"
                "Ask the user whether to overwrite, then call transcribe_audio "
                "again with overwrite=True."
            ),
            "existing_file": str(transcript_path),
        }

    summary = transcribe_to_file(path, out_dir, language)
    summary["transcript"] = transcript_path.read_text(encoding="utf-8")
    summary["source"] = str(path)
    return summary


@mcp.tool()
def process_url(
    url: str,
    output_dir: Optional[str] = None,
    video: bool = False,
    language: Optional[str] = None,
    markdown: bool = False,
) -> dict:
    """
    Full pipeline for one URL: download audio, then produce a transcript.

    Steps:
      1. Fetch metadata + download audio (mp3).
      2. Try site-provided subtitles via yt-dlp.
      3. If none, transcribe locally with Qwen3-ASR (auto-splitting long audio).

    Existing artifacts are reused (skipped), so re-running resumes cleanly.
    """
    from .pipeline import process_url as _pipeline_process_url

    result = _pipeline_process_url(
        url,
        base_output=_resolve_output(output_dir),
        video=video,
        language=language,
        markdown=markdown,
    )
    # Enrich with the standard file listing that MCP consumers expect.
    out_dir = Path(result["output_dir"])
    result["files"] = _list_outputs(out_dir)
    return result


@mcp.tool()
def unload_asr() -> dict:
    """
    Unload the Qwen3-ASR model and free its GPU memory.

    Call this when transcription work is finished — after processing one or
    more videos/files — so the GPU is released for other work. Safe to call
    even if the model was never loaded. The next transcription reloads it
    automatically (with the usual first-call load time).
    """
    from . import asr
    freed = asr.unload()
    return {
        "unloaded": freed,
        "message": (
            "ASR model unloaded; GPU memory freed."
            if freed
            else "ASR model was not loaded; nothing to free."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
