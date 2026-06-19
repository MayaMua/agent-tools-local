"""
yt-dlp downloader: fetch metadata, download audio/video/subtitles from
YouTube, Bilibili, and 1000+ other sites.

Supports CookieCloud for authenticated downloads and a direct-API fallback
for Bilibili (when yt-dlp returns HTTP 412).

Core library — usable from both CLI and the MCP server.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
import yt_dlp
from dotenv import load_dotenv

# Load site-level .env (not package-level) for CookieCloud credentials.
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH)

# ═══════════════════════════════════════════════════════════════════════════
# CookieCloud — authenticated download support
# ═══════════════════════════════════════════════════════════════════════════

_cached_cookies_path: str | None = None
_cookies_resolved: bool = False


def _fetch_cookiecloud_cookies() -> str | None:
    """Fetch cookies from CookieCloud and write a Netscape cookie file.

    Returns the file path, or None if CookieCloud is not configured / unreachable.
    """
    url = os.environ.get("COOKIECLOUD_URL", "").rstrip("/")
    uuid = os.environ.get("COOKIECLOUD_UUID", "")
    password = os.environ.get("COOKIECLOUD_PASSWORD", "")
    if not (url and uuid and password):
        return None
    try:
        resp = requests.post(
            f"{url}/get/{uuid}",
            json={"password": password},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [cookiecloud] failed to fetch cookies: {e}")
        return None

    cookie_data = data.get("cookie_data", {})
    if not cookie_data:
        print("  [cookiecloud] no cookie data returned")
        return None

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix="_cookies.txt", delete=False, encoding="utf-8"
    )
    tmp.write("# Netscape HTTP Cookie File\n")
    for domain, cookies in cookie_data.items():
        for c in cookies:
            cookie_domain = c.get("domain", domain)
            include_subdomains = "TRUE" if cookie_domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure", False) else "FALSE"
            expires = int(c.get("expirationDate", 0) or 0)
            name = c.get("name", "")
            value = c.get("value", "")
            tmp.write(
                f"{cookie_domain}\t{include_subdomains}\t{path}\t"
                f"{secure}\t{expires}\t{name}\t{value}\n"
            )
    tmp.close()
    print(f"  [cookiecloud] cookies loaded → {tmp.name}", file=sys.stderr)
    return tmp.name


def get_cookies_file() -> str | None:
    """Return a CookieCloud-sourced Netscape cookie file path (fetched once per process)."""
    global _cached_cookies_path, _cookies_resolved
    if not _cookies_resolved:
        _cached_cookies_path = _fetch_cookiecloud_cookies()
        _cookies_resolved = True
    return _cached_cookies_path


# ═══════════════════════════════════════════════════════════════════════════
# Bilibili direct-API fallback (yt-dlp returns HTTP 412 on some requests)
# ═══════════════════════════════════════════════════════════════════════════

def is_bilibili_url(url: str) -> bool:
    return "bilibili.com" in url.lower()


def _bilibili_get_cid(bvid: str) -> int:
    """Get video cid from bilibili view API."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    }
    r = requests.get(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Bilibili view API error: {data.get('message', 'unknown')}")
    return data["data"]["cid"]


def _bilibili_fetch_playinfo(bvid: str, cid: int) -> dict:
    """Fetch playurl data for the lowest-quality audio stream."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    }
    url = (
        f"https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={cid}&fnval=4048&fnver=0&fourk=1&try_look=1"
    )
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"Bilibili playurl API error: {data.get('message', 'unknown')}"
        )
    return data["data"]


def bilibili_download_audio(url: str, out_dir: Path, fmt: str = "mp3") -> None:
    """Direct Bilibili audio download via public API (bypasses yt-dlp 412).

    Uses worstaudio quality so no VIP is required.
    """
    m = re.search(r"BV[a-zA-Z0-9]+", url)
    if not m:
        print("  [bilibili] Could not extract BVID, skipping.")
        return
    bvid = m.group(0)
    print(f"  [bilibili] BVID: {bvid}")

    cid = _bilibili_get_cid(bvid)
    print(f"  [bilibili] CID: {cid}")

    info = _bilibili_fetch_playinfo(bvid, cid)
    dash = info.get("dash")
    if not dash or not dash.get("audio"):
        print("  [bilibili] No audio streams found.")
        return

    audios = sorted(dash["audio"], key=lambda a: a.get("id", 0), reverse=True)
    worst = audios[0]  # lowest quality = highest id in bilibili's scheme
    audio_url = worst.get("baseUrl") or worst.get("backupUrl", [None])[0]
    if not audio_url:
        print("  [bilibili] No downloadable audio URL.")
        return

    quality_desc = (
        f"{worst.get('bandwidth', '?')} bps, codec: {worst.get('codecs', '?')}"
    )
    print(f"  [bilibili] Audio quality: {quality_desc}")

    # Download raw m4s
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    }
    raw_path = out_dir / "audio.m4s"
    r = requests.get(
        audio_url.replace("\\u0026", "&"), headers=headers, stream=True, timeout=120
    )
    r.raise_for_status()
    with open(raw_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    print(f"  [bilibili] Downloaded: {raw_path.stat().st_size / 1024 / 1024:.1f} MB")

    # Convert to target format
    out_path = out_dir / f"audio.{fmt}"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw_path),
            "-c:a", "libmp3lame", "-q:a", "9", str(out_path),
        ],
        capture_output=True,
        check=False,
    )
    if out_path.exists():
        raw_path.unlink(missing_ok=True)
        print(
            f"  [audio] saved → {out_path} "
            f"({out_path.stat().st_size / 1024 / 1024:.1f} MB)"
        )
    else:
        print(f"  [bilibili] ffmpeg conversion failed, raw file at {raw_path}")


# ═══════════════════════════════════════════════════════════════════════════
# URL parsing & filename helpers
# ═══════════════════════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    """Remove characters that are unsafe for directory/file names."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:100]  # cap length


def parse_urls(inputs: list[str]) -> list[str]:
    """Accept a mix of raw URLs and .txt file paths. Returns deduplicated URLs."""
    urls = []
    seen = set()
    for item in inputs:
        path = Path(item)
        if path.exists() and path.suffix.lower() == ".txt":
            with open(path, encoding="utf-8") as f:
                for line in f:
                    url = line.strip()
                    if url and not url.startswith("#") and url not in seen:
                        urls.append(url)
                        seen.add(url)
        else:
            url = item.strip()
            if url and url not in seen:
                urls.append(url)
                seen.add(url)
    return urls


# ═══════════════════════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════════════════════

def fetch_info(url: str, cookies_file: str | None = None) -> dict:
    """Fetch video metadata without downloading."""
    cookies_file = cookies_file or get_cookies_file()
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
        return ydl.sanitize_info(info)


def make_output_dir(base_output: Path, info: dict) -> Path:
    """Create output directory: <sanitized_title>+<upload_date>."""
    title = sanitize_filename(info.get("title", "unknown"))
    upload_date = info.get("upload_date")  # YYYYMMDD or None
    ts = upload_date if upload_date else datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    folder_name = f"{ts}_{title}"
    out_dir = base_output / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def save_metadata(out_dir: Path, info: dict) -> None:
    """Save trimmed metadata to metadata.json."""
    meta = {
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("channel") or info.get("creator"),
        "tags": info.get("tags") or [],
        "upload_date": info.get("upload_date"),
        "download_date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "length_seconds": info.get("duration"),
        "url": info.get("webpage_url") or info.get("url"),
    }
    meta_path = out_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  [metadata] saved → {meta_path}")


# ═══════════════════════════════════════════════════════════════════════════
# Download helpers
# ═══════════════════════════════════════════════════════════════════════════

def _ydl_download(
    ydl_opts: dict,
    url: str,
    cookies_file: str | None,
    out_dir: Path | None = None,
    audio_fmt: str | None = None,
) -> None:
    """Run yt-dlp download with cookie fallback and Bilibili 412 handling."""
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as e:
        err_str = str(e)
        if "HTTP 412" in err_str and is_bilibili_url(url):
            print("  [yt-dlp] HTTP 412 on Bilibili — switching to direct API fallback")
            bilibili_download_audio(url, out_dir, audio_fmt or "mp3")
            return
        if cookies_file and "Requested format is not available" in err_str:
            ydl_opts.pop("cookiefile", None)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        else:
            raise


def download_audio(
    url: str, out_dir: Path, fmt: str = "mp3", cookies_file: str | None = None
) -> None:
    """Download audio and convert to the specified format."""
    cookies_file = cookies_file or get_cookies_file()
    output_template = str(out_dir / "audio.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": fmt,
                "preferredquality": "192",
            }
        ],
        "quiet": False,
        "no_warnings": False,
    }
    _ydl_download(ydl_opts, url, cookies_file, out_dir, audio_fmt=fmt)
    audio_path = out_dir / f"audio.{fmt}"
    if audio_path.exists():
        print(f"  [audio] saved → {audio_path}")


def download_video(
    url: str, out_dir: Path, fmt: str = "mp4", cookies_file: str | None = None
) -> None:
    """Download video in the specified container format."""
    cookies_file = cookies_file or get_cookies_file()
    output_template = str(out_dir / "video.%(ext)s")
    ydl_opts = {
        "format": (
            f"bestvideo[ext={fmt}]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        ),
        "outtmpl": output_template,
        "merge_output_format": fmt,
        "quiet": False,
        "no_warnings": False,
    }
    _ydl_download(ydl_opts, url, cookies_file, out_dir)
    video_path = out_dir / f"video.{fmt}"
    if video_path.exists():
        print(f"  [video] saved → {video_path}")


def _vtt_to_text(vtt_path: Path) -> str:
    """Convert a VTT subtitle file to plain text, deduplicating consecutive lines."""
    lines = []
    last_line = None
    time_re = re.compile(r"^\d{2}:\d{2}[\d:,.]* --> ")

    with open(vtt_path, encoding="utf-8") as f:
        in_header = True
        for raw in f:
            line = raw.strip()
            if in_header:
                if line == "" or line.startswith("WEBVTT"):
                    continue
                in_header = False
            if not line or time_re.match(line) or line.isdigit():
                continue
            line = re.sub(r"<[^>]+>", "", line).strip()
            if line and line != last_line:
                lines.append(line)
                last_line = line

    return "\n".join(lines)


def download_transcript(
    url: str, out_dir: Path, cookies_file: str | None = None
) -> None:
    """Download auto-generated or manual subtitles, write cleaned transcript.txt."""
    cookies_file = cookies_file or get_cookies_file()
    tmp_prefix = str(out_dir / "sub")
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": ["en", "zh-Hans", "zh-Hant", "zh"],
        "outtmpl": tmp_prefix + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
    }
    try:
        _ydl_download(ydl_opts, url, cookies_file)
    except Exception as e:
        print(f"  [transcript] download warning: {e}")

    vtt_files = sorted(out_dir.glob("*.vtt"))
    if not vtt_files:
        print("  [transcript] no subtitles found for this video.")
        return

    transcript_path = out_dir / "transcript.txt"
    with open(transcript_path, "w", encoding="utf-8") as out_f:
        for vtt_file in vtt_files:
            out_f.write(f"=== {vtt_file.name} ===\n\n")
            text = _vtt_to_text(vtt_file)
            out_f.write(text)
            out_f.write("\n\n")
            vtt_file.unlink()

    print(f"  [transcript] saved → {transcript_path}")


# ═══════════════════════════════════════════════════════════════════════════
# Bulk download (CLI only — not used by MCP server)
# ═══════════════════════════════════════════════════════════════════════════

def bulk_download(
    urls: list[str],
    base_output: Path,
    audio_fmt: str | None = None,
    video_fmt: str | None = None,
    transcript: bool = False,
    cookies_file: str | None = None,
) -> None:
    """Download media for multiple URLs (one per line in the output)."""
    for url in urls:
        print(f"\n{'='*60}\nProcessing: {url}")

        if is_bilibili_url(url):
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            m = re.search(r"BV[a-zA-Z0-9]+", url)
            folder_name = f"{ts}_bilibili_{m.group(0) if m else 'unknown'}"
            out_dir = base_output / folder_name
            out_dir.mkdir(parents=True, exist_ok=True)
            print(f"  Output dir: {out_dir}")
            if audio_fmt:
                try:
                    bilibili_download_audio(url, out_dir, audio_fmt)
                except Exception as e:
                    print(f"  [ERROR] bilibili audio download failed: {e}")
            if video_fmt:
                print("  [WARN] Bilibili video download not supported — audio only.")
            if transcript:
                print("  [WARN] Bilibili transcript download not supported.")
            continue

        try:
            info = fetch_info(url, cookies_file=cookies_file)
        except Exception as e:
            print(f"  [ERROR] could not fetch info: {e}")
            continue

        out_dir = make_output_dir(base_output, info)
        print(f"  Output dir: {out_dir}")
        save_metadata(out_dir, info)

        if audio_fmt:
            try:
                download_audio(url, out_dir, audio_fmt, cookies_file=cookies_file)
            except Exception as e:
                print(f"  [ERROR] audio download failed: {e}")

        if video_fmt:
            try:
                download_video(url, out_dir, video_fmt, cookies_file=cookies_file)
            except Exception as e:
                print(f"  [ERROR] video download failed: {e}")

        if transcript:
            try:
                download_transcript(url, out_dir, cookies_file=cookies_file)
            except Exception as e:
                print(f"  [ERROR] transcript download failed: {e}")

    print(f"\n{'='*60}\nAll done.")


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download video/audio/transcript via yt-dlp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "inputs", nargs="+", metavar="URL_OR_FILE",
        help="One or more URLs or paths to .txt files containing URLs (one per line).",
    )
    parser.add_argument(
        "--audio", metavar="FORMAT", default=None,
        help="Download audio and convert to FORMAT (e.g. mp3, m4a, wav).",
    )
    parser.add_argument(
        "--video", metavar="FORMAT", default=None,
        help="Download video in FORMAT (e.g. mp4, mkv, webm).",
    )
    parser.add_argument(
        "--transcript", action="store_true",
        help="Download subtitles / auto-generated transcript.",
    )
    parser.add_argument(
        "--output", "-o", metavar="DIR", default="./data",
        help="Base output directory (default: ./data).",
    )

    args = parser.parse_args()

    if not args.audio and not args.video and not args.transcript:
        parser.error(
            "At least one of --audio, --video, or --transcript must be specified."
        )

    urls = parse_urls(args.inputs)
    if not urls:
        print("No URLs found in inputs.", file=sys.stderr)
        sys.exit(1)

    base_output = Path(args.output)
    base_output.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(urls)} URL(s). Output base: {base_output.resolve()}")

    bulk_download(
        urls=urls,
        base_output=base_output,
        audio_fmt=args.audio,
        video_fmt=args.video,
        transcript=args.transcript,
    )


if __name__ == "__main__":
    main()
