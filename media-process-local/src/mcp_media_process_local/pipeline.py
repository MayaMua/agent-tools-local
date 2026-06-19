"""
Pipeline: video URL → metadata + audio + transcript.

CLI:  uv run python -m mcp_media_process_local.pipeline <url-or-links-file>
API:  from mcp_media_process_local.pipeline import process_url

Steps per URL:
  1. Fetch metadata, download audio (MP3)
  2. Try site-provided subtitles via yt-dlp
  3. If no subtitles: transcribe locally with Qwen3-ASR
     (audio >12 min is auto-split into 10-min segments, 10s overlap)
"""

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import asr as _asr
from .downloader import (
    bilibili_download_audio,
    download_audio,
    download_transcript,
    download_video,
    fetch_info,
    is_bilibili_url,
    make_output_dir,
    parse_urls,
    sanitize_filename,
    save_metadata,
)

DEFAULT_OUTPUT = Path("./output")
ASR_SPLIT_THRESHOLD_MIN = 12
SEGMENT_SECONDS = 600  # 10 minutes
OVERLAP_SECONDS = 10


# ═══════════════════════════════════════════════════════════════════════════
# Audio helpers
# ═══════════════════════════════════════════════════════════════════════════

def get_audio_duration_sec(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    return float(r.stdout.strip())


def split_audio(audio_path: Path, seg_dir: Path) -> list[Path]:
    """Split into 10-min MP3 segments with 10s overlap using ffmpeg."""
    duration = get_audio_duration_sec(audio_path)
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg_s, overlap_s = SEGMENT_SECONDS, OVERLAP_SECONDS
    stride_s = seg_s - overlap_s
    parts, start_s, idx = [], 0, 1
    while start_s < duration:
        end_s = min(start_s + seg_s, duration)
        out = seg_dir / f"{audio_path.stem}_part{idx}.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start_s), "-to", str(end_s),
                "-i", str(audio_path),
                "-c:a", "libmp3lame", "-q:a", "2", str(out),
            ],
            capture_output=True, text=True, timeout=300,
        )
        print(f"    segment: {out.name} ({start_s:.0f}s-{end_s:.0f}s)")
        parts.append(out)
        idx += 1
        start_s += stride_s
    return parts


def transcribe_to_file(
    audio_path: Path,
    out_dir: Path,
    language: Optional[str] = None,
) -> dict:
    """Transcribe audio (splitting if long) and write transcript.txt.

    Returns a summary dict: transcript_path, characters, segments, duration_min.
    """
    duration_min = get_audio_duration_sec(audio_path) / 60

    if duration_min <= ASR_SPLIT_THRESHOLD_MIN:
        text = _asr.transcribe(audio_path, language)
        segments = [(audio_path.name, text)]
    else:
        seg_dir = out_dir / f"{audio_path.stem}_segments"
        parts = split_audio(audio_path, seg_dir)
        segments = []
        for part in parts:
            t = _asr.transcribe(part, language)
            print(f"    {part.name}: {len(t)} chars")
            segments.append((part.name, t))

    transcript_path = out_dir / "transcript.txt"
    with open(transcript_path, "w", encoding="utf-8") as f:
        for name, text in segments:
            f.write(f"{text}\n\n")

    total = sum(len(t) for _, t in segments)
    print(f"  [asr] saved: {total} chars → {transcript_path}")
    return {
        "transcript_path": str(transcript_path),
        "characters": total,
        "segments": len(segments),
        "duration_min": round(duration_min, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main pipeline entry point
# ═══════════════════════════════════════════════════════════════════════════

def process_url(
    url: str,
    base_output: Path | None = None,
    video: bool = False,
    language: Optional[str] = None,
    markdown: bool = False,
) -> dict:
    """Download + transcribe a single media URL.

    Args:
        url: Media page URL (YouTube, Bilibili, any yt-dlp site).
        base_output: Base directory for per-video output folders. Default: ./output.
        video: Also download the video file (mp4).
        language: Optional language hint for ASR (e.g. 'zh', 'en').
        markdown: Also write a combined {title}.md with metadata + transcript.

    Returns:
        Dict with: output_dir, title, transcript_method, transcript_chars,
        transcript_preview, files, (markdown_path), (warnings).
    """
    base = base_output or DEFAULT_OUTPUT
    base.mkdir(parents=True, exist_ok=True)
    result: dict = {"warnings": []}

    # --- Bilibili (direct API, no yt-dlp metadata) ---
    if is_bilibili_url(url):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        m = re.search(r"BV[a-zA-Z0-9]+", url)
        folder = f"{ts}_bilibili_{m.group(0) if m else 'unknown'}"
        out_dir = base / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        result["site"] = "bilibili"

        audio_path = out_dir / "audio.mp3"
        if not audio_path.exists():
            try:
                bilibili_download_audio(url, out_dir, "mp3")
            except Exception as e:
                result["warnings"].append(f"Audio download failed: {e}")

        transcript_method = "none"
        if audio_path.exists() and not (out_dir / "transcript.txt").exists():
            try:
                transcribe_to_file(audio_path, out_dir, language)
                transcript_method = "asr"
            except Exception as e:
                result["warnings"].append(f"ASR failed: {e}")
        elif (out_dir / "transcript.txt").exists():
            transcript_method = "asr"

        result = _assemble_result(out_dir, transcript_method, None, result)
        if markdown:
            result["markdown_path"] = str(write_markdown(out_dir, result.get("title", "Untitled")) or "")
        return result

    # --- Standard yt-dlp flow ---
    try:
        info = fetch_info(url)
    except Exception as e:
        result["error"] = f"Metadata fetch failed: {e}"
        return result

    out_dir = make_output_dir(base, info)
    if not (out_dir / "metadata.json").exists():
        save_metadata(out_dir, info)

    audio_path = out_dir / "audio.mp3"
    if not audio_path.exists():
        try:
            download_audio(url, out_dir, "mp3")
        except Exception as e:
            result["warnings"].append(f"Audio download failed: {e}")

    if video and not list(out_dir.glob("video.*")):
        try:
            download_video(url, out_dir, "mp4")
        except Exception as e:
            result["warnings"].append(f"Video download failed: {e}")

    # 1) site subtitles → 2) ASR fallback
    transcript_path = out_dir / "transcript.txt"
    transcript_method = "none"
    if not transcript_path.exists():
        try:
            download_transcript(url, out_dir)
        except Exception as e:
            print(f"  [transcript] yt-dlp failed: {e}")

    if transcript_path.exists():
        transcript_method = "subtitles"
    elif audio_path.exists():
        try:
            transcribe_to_file(audio_path, out_dir, language)
            transcript_method = "asr"
        except Exception as e:
            result["warnings"].append(f"ASR failed: {e}")
    else:
        result["warnings"].append("No audio available to transcribe.")

    result = _assemble_result(out_dir, transcript_method, info.get("title"), result)
    if markdown:
        result["markdown_path"] = str(write_markdown(out_dir, result.get("title", "Untitled")) or "")
    return result


def _assemble_result(
    out_dir: Path, method: str, title: Optional[str], result: dict
) -> dict:
    """Assemble the process_url return payload from an output directory."""
    transcript_path = out_dir / "transcript.txt"
    text = (
        transcript_path.read_text(encoding="utf-8")
        if transcript_path.exists()
        else ""
    )

    files = {}
    for key, name in (
        ("audio", "audio.mp3"),
        ("metadata", "metadata.json"),
        ("transcript", "transcript.txt"),
    ):
        p = out_dir / name
        if p.exists():
            files[key] = str(p)
    # Markdown file — named after the video title
    md_files = sorted(out_dir.glob("*.md"))
    if md_files:
        files["markdown"] = str(md_files[0])
    videos = sorted(out_dir.glob("video.*"))
    if videos:
        files["video"] = str(videos[0])

    result.update({
        "output_dir": str(out_dir),
        "title": title,
        "transcript_method": method,
        "transcript_chars": len(text),
        "transcript_preview": text[:500],
        "files": files,
    })
    if not result.get("warnings"):
        result.pop("warnings", None)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Combined Markdown output (metadata + transcript in one file)
# ═══════════════════════════════════════════════════════════════════════════

def write_markdown(out_dir: Path, title: str) -> Path | None:
    """Write a combined {title}.md with metadata + transcript.

    Reads metadata.json and transcript.txt from out_dir, produces a single
    Markdown file named after the video title. Returns the file path, or
    None if neither source file exists.
    """
    meta_path = out_dir / "metadata.json"
    transcript_path = out_dir / "transcript.txt"

    if not meta_path.exists() and not transcript_path.exists():
        return None

    lines = []

    # Metadata block (title lives in metadata.json, not repeated as heading)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("author"):
            lines.append(f"**Author:** {meta['author']}")
        if meta.get("upload_date"):
            lines.append(f"**Published:** {meta['upload_date']}")
        if meta.get("length_seconds"):
            mins, secs = divmod(int(meta["length_seconds"]), 60)
            lines.append(f"**Duration:** {mins}:{secs:02d}")
        if meta.get("url"):
            lines.append(f"**URL:** {meta['url']}")
        if meta.get("tags"):
            lines.append(f"**Tags:** {', '.join(meta['tags'])}")

    # Transcript
    if transcript_path.exists():
        text = transcript_path.read_text(encoding="utf-8").strip()
        if text:
            lines.append("\n---\n")
            lines.append(text)

    if len(lines) == 0:  # no metadata and no transcript
        return None

    md_name = sanitize_filename(title)
    md_path = out_dir / f"{md_name}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  [markdown] saved → {md_path}")
    return md_path


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point (python -m mcp_media_process_local.pipeline ...)
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Download + transcribe media from URL(s) or a links file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "inputs", nargs="+", metavar="URL_OR_FILE",
        help="URLs or .txt file(s) of URLs",
    )
    parser.add_argument(
        "--output", "-o", default=str(DEFAULT_OUTPUT),
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--video", action="store_true",
        help="Also download video (mp4)",
    )
    parser.add_argument(
        "--language", "-l", default=None,
        help="Language hint for ASR (e.g. zh, en)",
    )
    parser.add_argument(
        "--markdown", action="store_true",
        help="Also write a combined {title}.md with metadata + transcript",
    )
    args = parser.parse_args()

    urls = parse_urls(args.inputs)
    if not urls:
        print("No URLs found.", file=sys.stderr)
        sys.exit(1)

    base_output = Path(args.output)
    base_output.mkdir(parents=True, exist_ok=True)
    print(f"Processing {len(urls)} URL(s) → {base_output}")

    try:
        for url in urls:
            result = process_url(url, base_output, video=args.video, language=args.language, markdown=args.markdown)
            # Print summary
            if result.get("error"):
                print(f"  [ERROR] {result['error']}")
            else:
                print(f"  title: {result.get('title', 'N/A')}")
                print(f"  method: {result.get('transcript_method')}")
                print(
                    f"  transcript: {result.get('transcript_chars', 0)} chars"
                    f"  → {result.get('output_dir')}/transcript.txt"
                )
            # Surface anything that quietly went wrong (e.g. an ASR error that
            # left transcript_method == "none") instead of hiding it.
            for w in result.get("warnings", []):
                print(f"  [WARN] {w}")
    finally:
        # Job done (one or more videos): free the GPU instead of leaving the
        # vLLM engine resident until process exit.
        if _asr.unload():
            print("  [asr] model unloaded, GPU freed.")

    print(f"\n{'='*60}\nDone.")


if __name__ == "__main__":
    main()
