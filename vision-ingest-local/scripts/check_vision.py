#!/usr/bin/env python3
"""Check if a local LLM server is reachable and the configured model supports vision."""

import os
import sys
import json
import subprocess
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urljoin


def load_env_file():
    """Load .env file, preferring cwd then script dir then home."""
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


def http_get(url, timeout=10):
    """Simple HTTP GET returning parsed JSON."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── Ollama ──────────────────────────────────────────────────────────────────

def check_ollama(url, model):
    """Verify Ollama server is up and the model supports vision.

    Returns (ok: bool, message: str).
    """
    # 1. Check server health
    try:
        http_get(urljoin(url, "/api/tags"), timeout=5)
    except urllib.error.URLError as e:
        return False, (
            f"Cannot reach Ollama at {url}\n"
            f"  Error: {e.reason}\n"
            f"  Is the container running? Check with: docker ps | grep ollama"
        )

    # 2. List models and find ours
    try:
        data = http_get(urljoin(url, "/api/tags"))
    except Exception as e:
        return False, f"Failed to list models: {e}"

    models = {m["name"]: m for m in data.get("models", [])}

    if model not in models:
        close = [n for n in models if model.split(":")[0] in n]
        msg = f"Model '{model}' not found on server.\n"
        if close:
            msg += f"  Did you mean one of: {', '.join(close)}?\n"
        msg += "  Pull it with:  ollama pull <model-name>\n"
        msg += f"  Available: {', '.join(models.keys())}"
        return False, msg

    info = models[model]
    caps = set(info.get("capabilities", []))

    if "vision" not in caps:
        return False, (
            f"Model '{model}' does NOT support vision.\n"
            f"  Capabilities: {', '.join(sorted(caps)) if caps else 'none reported'}\n"
            f"  Run discover.py to find vision-capable models.\n"
            f"  Or pull a vision model:  ollama pull llava:latest"
        )

    param_size = info.get("details", {}).get("parameter_size", "?")
    family = info.get("details", {}).get("family", "?")
    size_gb = info.get("size", 0) / 1e9

    return True, (
        f"✓ Server:  {url}\n"
        f"✓ Model:   {model}\n"
        f"✓ Vision:  supported\n"
        f"  Family:  {family}  |  Params: {param_size}  |  Size: {size_gb:.1f} GB"
    )


# ── LM Studio ───────────────────────────────────────────────────────────────

def _lms_ls_json():
    """Run `lms ls --json` and return parsed list, or None on failure."""
    try:
        result = subprocess.run(
            ["lms", "ls", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


def check_lmstudio(url, model):
    """Verify LM Studio server is up and the model is loaded + vision-capable.

    Uses `lms ls --json` for definitive vision detection when available,
    falling back to /v1/models health check + architecture heuristics.

    Returns (ok: bool, message: str).
    """
    # 1. Try `lms ls --json` for definitive answer
    lms_data = _lms_ls_json()
    if lms_data:
        llms = [m for m in lms_data if m.get("type") == "llm"]
        by_key = {m["modelKey"]: m for m in llms}

        # Try exact match first, then fuzzy
        match = by_key.get(model)
        if not match:
            # Try matching without variant suffix
            for key, m in by_key.items():
                if model in key or key in model:
                    match = m
                    break

        if not match:
            return False, (
                f"Model '{model}' not found in `lms ls`.\n"
                f"  Available: {', '.join(by_key.keys())}\n"
                f"  Download it with:  lms get <model>"
            )

        name = match["modelKey"]
        arch = match.get("architecture", "?")
        params = match.get("paramsString", "?")
        size_gb = match.get("sizeBytes", 0) / 1e9
        has_vision = match.get("vision", False)

        if not has_vision:
            return False, (
                f"Model '{name}' does NOT support vision.\n"
                f"  Architecture: {arch}  |  Params: {params}\n"
                f"  Run discover.py to find vision-capable models."
            )

        # Also check if it's loaded on the server
        loaded_note = ""
        try:
            api_data = http_get(urljoin(url, "/v1/models"), timeout=5)
            loaded_ids = {m["id"] for m in api_data.get("data", [])}
            if name not in loaded_ids and model not in loaded_ids:
                loaded_note = (
                    f"\n  ⚠ Model is not currently loaded on the server.\n"
                    f"  Load it with:  lms load {name}"
                )
        except Exception:
            loaded_note = (
                f"\n  ⚠ Could not check server — is LM Studio running?\n"
                f"  Start with:  lms server start"
            )

        return True, (
            f"✓ Model:   {name}\n"
            f"✓ Vision:  supported (confirmed by LM Studio)\n"
            f"  Arch:    {arch}  |  Params: {params}  |  Size: {size_gb:.1f} GB"
            f"{loaded_note}"
        )

    # 2. Fallback: /v1/models health check only
    try:
        data = http_get(urljoin(url, "/v1/models"), timeout=5)
    except urllib.error.URLError as e:
        return False, (
            f"Cannot reach LM Studio at {url}\n"
            f"  Error: {e.reason}\n"
            f"  Start the server:  lms server start\n"
            f"  Check status:      lms server status"
        )

    loaded = {m["id"]: m for m in data.get("data", [])}

    if model not in loaded:
        close = [n for n in loaded if any(p in n for p in model.split("/"))]
        msg = f"Model '{model}' is not loaded on the server.\n"
        if close:
            msg += f"  Did you mean: {close[0]}?\n"
        msg += f"  Loaded: {', '.join(loaded.keys()) or 'none'}\n"
        msg += "  Load with:  lms load <model-name>"
        return False, msg

    # We can't definitively check vision from /v1/models alone
    return True, (
        f"✓ Server:  {url} (reachable)\n"
        f"✓ Model:   {model} (loaded)\n"
        f"⚠ Vision:  unable to confirm via API — run `lms ls` or a test image\n"
        f"  Install `lms` CLI for definitive vision checks."
    )


# ── main ────────────────────────────────────────────────────────────────────

def resolve_server_url(backend, cli_url=None):
    """Pick the server URL for the chosen backend.

    Backend-specific env vars take precedence:
      lmstudio -> LMS_SERVER_URL, ollama -> Ollama_SERVER_URL.
    Falls back to the legacy VISION_SERVER_URL, then a localhost default.
    """
    if cli_url:
        return cli_url
    if backend == "ollama":
        return (os.environ.get("Ollama_SERVER_URL")
                or os.environ.get("VISION_SERVER_URL")
                or "http://localhost:11434")
    return (os.environ.get("LMS_SERVER_URL")
            or os.environ.get("VISION_SERVER_URL")
            or "http://localhost:1234")


def main():
    parser = argparse.ArgumentParser(
        description="Check if a local LLM model supports vision"
    )
    parser.add_argument("--backend", choices=["ollama", "lmstudio"],
                        help="Backend (default: from .env or ollama)")
    parser.add_argument("--url", help="Server URL (overrides .env)")
    parser.add_argument("--model", help="Model name (overrides .env)")
    args = parser.parse_args()

    load_env_file()

    backend = args.backend or os.environ.get("VISION_BACKEND", "ollama")
    url = resolve_server_url(backend, args.url)
    model = args.model or os.environ.get("VISION_MODEL", "")

    if not model:
        model = "glm-ocr:latest" if backend == "ollama" else "allenai/olmocr-2-7b"

    if backend == "ollama":
        ok, msg = check_ollama(url, model)
    else:
        ok, msg = check_lmstudio(url, model)

    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
