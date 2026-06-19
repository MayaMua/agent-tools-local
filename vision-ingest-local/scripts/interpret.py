#!/usr/bin/env python3
"""Send an image to a local LLM for vision-based interpretation.

Supports Ollama and LM Studio via their OpenAI-compatible /v1/chat/completions
endpoints. Outputs the model's text response to stdout.
"""

import os
import sys
import json
import base64
import argparse
import mimetypes
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urljoin


# ── helpers ─────────────────────────────────────────────────────────────────

def load_env_file():
    """Load .env, preferring cwd → script dir → ~/.config/local-vision."""
    search_dirs = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent,
        Path.home() / ".config" / "local-vision",
    ]
    for d in search_dirs:
        env_path = d / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        val = val.strip().strip('"').strip("'")
                        if key.strip() not in os.environ:
                            os.environ[key.strip()] = val
            return


def encode_image(image_path, max_dim=2048):
    """Load an image, optionally downsize, return (base64_str, mime_type).

    Downsamples images whose longest edge exceeds *max_dim* to avoid
    hitting model context limits.  Preserves aspect ratio.
    """
    path = Path(image_path)
    if not path.exists():
        print(f"❌ Image not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        # fall back to extension sniffing
        ext = path.suffix.lower()
        ext_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".tiff": "image/tiff", ".tif": "image/tiff",
        }
        mime = ext_map.get(ext, "image/png")

    try:
        from PIL import Image
        img = Image.open(path)
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)
            # Re-encode to bytes
            import io
            buf = io.BytesIO()
            fmt = mime.split("/")[-1].upper()
            if fmt == "JPG":
                fmt = "JPEG"
            img.save(buf, format=fmt)
            raw = buf.getvalue()
        else:
            raw = path.read_bytes()
    except ImportError:
        raw = path.read_bytes()

    b64 = base64.b64encode(raw).decode("ascii")
    return b64, mime


def build_payload(model, prompt, image_b64, mime_type, max_tokens, temperature):
    """Build the JSON payload for an OpenAI-compatible vision request."""
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_b64}",
                            "detail": "auto",
                        },
                    },
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }


def call_api(url, payload, timeout=120):
    """POST to the chat completions endpoint, return the response JSON."""
    endpoint = urljoin(url, "/v1/chat/completions")
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"❌ Server returned {e.code}", file=sys.stderr)
        print(f"   {body[:500]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"❌ Cannot reach server at {url}", file=sys.stderr)
        print(f"   {e.reason}", file=sys.stderr)
        sys.exit(1)


def extract_text(response):
    """Pull the assistant's text from an OpenAI-format response."""
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print(f"⚠ Unexpected response shape. Dumping raw JSON to stderr.", file=sys.stderr)
        json.dump(response, sys.stderr, indent=2)
        sys.exit(1)


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Interpret an image using a local LLM with vision"
    )
    parser.add_argument("--image", required=True, help="Path to the image file")
    parser.add_argument("--prompt", help="Question/prompt about the image")
    parser.add_argument("--backend", choices=["ollama", "lmstudio"],
                        help="Backend (default: from .env or ollama)")
    parser.add_argument("--url", help="Server URL (overrides .env)")
    parser.add_argument("--model", help="Model name (overrides .env)")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Max response tokens (default: 1024)")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Temperature 0.0-1.0 (default: 0.2)")
    parser.add_argument("--max-dim", type=int, default=2048,
                        help="Downscale images whose longest edge exceeds this (default: 2048)")
    parser.add_argument("--raw", action="store_true",
                        help="Output raw JSON instead of just the text")
    args = parser.parse_args()

    load_env_file()

    backend = args.backend or os.environ.get("VISION_BACKEND", "ollama")
    url = args.url or os.environ.get("VISION_SERVER_URL", "")
    model = args.model or os.environ.get("VISION_MODEL", "")
    prompt = args.prompt or os.environ.get(
        "VISION_DEFAULT_PROMPT", "Describe this image in detail."
    )
    max_tokens = args.max_tokens or int(os.environ.get("VISION_MAX_TOKENS", "1024"))
    temperature = args.temperature or float(os.environ.get("VISION_TEMPERATURE", "0.2"))

    if not url:
        url = "http://localhost:11434" if backend == "ollama" else "http://localhost:1234"
    if not model:
        model = "glm-ocr:latest" if backend == "ollama" else "allenai/olmocr-2-7b"

    # ── do the work ──
    print(f"🖼  Image:  {args.image}", file=sys.stderr)
    print(f"🌐 Server: {url}", file=sys.stderr)
    print(f"🧠 Model:  {model}", file=sys.stderr)
    print(f"💬 Prompt: {prompt[:80]}{'…' if len(prompt) > 80 else ''}", file=sys.stderr)
    print(file=sys.stderr)

    image_b64, mime_type = encode_image(args.image, max_dim=args.max_dim)
    payload = build_payload(model, prompt, image_b64, mime_type, max_tokens, temperature)

    print("⏳ Sending to model…", file=sys.stderr)
    response = call_api(url, payload)

    if args.raw:
        json.dump(response, sys.stdout, indent=2)
    else:
        text = extract_text(response)
        print(text)


if __name__ == "__main__":
    main()
