#!/usr/bin/env python3
"""Discover vision-capable models on a local LLM server (Ollama or LM Studio)."""

import os
import sys
import json
import subprocess
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urljoin


# Architecture substrings that strongly indicate vision/multimodal support.
# Used only as a fallback when the server doesn't expose capability info.
VISION_ARCH_PATTERNS = [
    "qwen2vl", "qwen3vl", "llava", "gemma4", "pixtral",
    "cogvlm", "fuyu", "paligemma", "molmo", "internvl",
    "minicpmv", "minicpm-v", "phi3-v", "phi-3.5-vision",
    "deepseek-vl", "yi-vl", "qwenvl", "mplug-owl",
    "idefics", "idefics2", "idefics3", "blip",
    "olmocr", "glmocr", "glm4v",
]


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

def discover_ollama(url):
    """Find vision-capable models on Ollama via /api/tags."""
    try:
        data = http_get(urljoin(url, "/api/tags"))
    except urllib.error.URLError as e:
        print(f"❌ Cannot reach Ollama at {url}")
        print(f"   {e.reason}")
        sys.exit(1)

    models = data.get("models", [])
    vision_models = []
    text_models = []

    for m in models:
        caps = set(m.get("capabilities", []))
        info = {
            "name": m["name"],
            "size_gb": m.get("size", 0) / 1e9,
            "family": m.get("details", {}).get("family", "unknown"),
            "param_size": m.get("details", {}).get("parameter_size", "?"),
        }
        if "vision" in caps:
            vision_models.append(info)
        else:
            text_models.append(info)

    print(f"Server: {url}  |  Models total: {len(models)}")
    print()

    if vision_models:
        print("🖼️  Vision-capable models:")
        for m in vision_models:
            print(f"   ✓ {m['name']:<40s}  {m['param_size']:>6s} params  "
                  f"({m['size_gb']:.1f} GB)  [{m['family']}]")
    else:
        print("⚠️  No vision-capable models found on this server.")
        print("   Pull one with:  ollama pull llava:latest")
        print("   Or:              ollama pull glm-ocr:latest")

    if text_models:
        print()
        print("📝 Text-only models (no vision):")
        for m in text_models:
            print(f"   ✗ {m['name']:<40s}  {m['param_size']:>6s} params  "
                  f"({m['size_gb']:.1f} GB)  [{m['family']}]")

    return vision_models


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


def _arch_guess_is_vision(architecture, model_key):
    """Heuristic vision check from architecture string and model key."""
    combined = f"{architecture} {model_key}".lower().replace("-", "").replace("_", "")
    return any(p in combined for p in VISION_ARCH_PATTERNS)


def discover_lmstudio(url):
    """Find vision-capable models on LM Studio.

    Uses `lms ls --json` when available (shows a definitive ``vision`` flag),
    falling back to /v1/models + architecture-name heuristics.
    """
    # ── Primary: lms ls --json ──
    lms_data = _lms_ls_json()
    if lms_data:
        llms = [m for m in lms_data if m.get("type") == "llm"]
        vision_models = []
        text_models = []

        for m in llms:
            info = {
                "name": m["modelKey"],
                "params": m.get("paramsString", "?"),
                "arch": m.get("architecture", "?"),
                "size_gb": m.get("sizeBytes", 0) / 1e9,
            }
            if m.get("vision"):
                vision_models.append(info)
            else:
                text_models.append(info)

        print(f"Source: lms ls --json  |  LLMs found: {len(llms)}")
        print()

        if vision_models:
            print("🖼️  Vision-capable models:")
            for m in vision_models:
                print(f"   ✓ {m['name']:<55s} {m['params']:>8s}  "
                      f"({m['size_gb']:.1f} GB)  [{m['arch']}]")
        else:
            print("⚠️  No vision-capable models found.")
            print("   Download one with:  lms get <model>")

        if text_models:
            print()
            print("📝 Text-only models:")
            for m in text_models:
                print(f"   ✗ {m['name']:<55s} {m['params']:>8s}  "
                      f"({m['size_gb']:.1f} GB)  [{m['arch']}]")

        # Also check what's currently loaded on the server
        try:
            api_data = http_get(urljoin(url, "/v1/models"))
            loaded = [m["id"] for m in api_data.get("data", [])]
            loaded_vision = [m["name"] for m in vision_models if m["name"] in loaded]
            loaded_text = [m["name"] for m in text_models if m["name"] in loaded]

            if loaded_vision or loaded_text:
                print()
                print("📡 Currently loaded on server:")
                for name in loaded_vision:
                    print(f"   🟢 {name}  (vision ✓)")
                for name in loaded_text:
                    print(f"   🟡 {name}  (text-only)")
                not_loaded = [m["name"] for m in vision_models
                              if m["name"] not in loaded]
                if not_loaded:
                    print()
                    print("💤 Vision models available but not loaded:")
                    for name in not_loaded:
                        print(f"   Load with:  lms load {name}")
        except Exception:
            pass  # server not running — that's fine

        return vision_models

    # ── Fallback: /v1/models + heuristic ──
    try:
        data = http_get(urljoin(url, "/v1/models"))
    except urllib.error.URLError as e:
        print(f"❌ Cannot reach LM Studio at {url}")
        print(f"   {e.reason}")
        print(f"   Start the server with:  lms server start")
        sys.exit(1)

    models = data.get("data", [])
    if not models:
        print("No models loaded in LM Studio.")
        print("Load a model with:  lms load <model-name>")
        print("List available:      lms ls")
        return []

    vision_models = []
    text_models = []

    for m in models:
        name = m["id"]
        is_vision = _arch_guess_is_vision("", name)
        info = {"name": name}
        if is_vision:
            vision_models.append(info)
        else:
            text_models.append(info)

    print(f"Server: {url}  |  Loaded models: {len(models)}")
    print("(Heuristic detection — install `lms` CLI for definitive results)")
    print()

    if vision_models:
        print("🖼️  Likely vision-capable:")
        for m in vision_models:
            print(f"   ✓ {m['name']}")
    else:
        print("⚠️  No models with known vision architectures detected.")

    if text_models:
        print()
        print("📝 Probably text-only:")
        for m in text_models:
            print(f"   ? {m['name']}")

    return vision_models


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discover vision-capable local models")
    parser.add_argument("--backend", choices=["ollama", "lmstudio"],
                        help="Backend to query (default: from .env or ollama)")
    parser.add_argument("--url", help="Server URL (overrides .env)")
    args = parser.parse_args()

    load_env_file()

    backend = args.backend or os.environ.get("VISION_BACKEND", "ollama")

    if backend == "ollama":
        url = args.url or os.environ.get("VISION_SERVER_URL", "http://localhost:11434")
        discover_ollama(url)
    elif backend == "lmstudio":
        url = args.url or os.environ.get("VISION_SERVER_URL", "http://localhost:1234")
        discover_lmstudio(url)


if __name__ == "__main__":
    main()
