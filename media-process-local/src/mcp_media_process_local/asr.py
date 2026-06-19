"""
Local GPU speech-to-text transcription via Qwen3-ASR.

Model: Qwen/Qwen3-ASR-1.7B with Qwen/Qwen3-ForcedAligner-0.6B
Requires: NVIDIA GPU, CUDA, qwen-asr[vllm] package.

The model is loaded lazily on first transcribe() call and cached for the
process lifetime.  Import is deferred so GPU memory is not consumed at
MCP server startup.
"""

import atexit
import os
import signal
from pathlib import Path

# Model identifiers — public so callers can reference them.
ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
FORCED_ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"

_model = None

# Qwen3-ASR validates against canonical English language *names* (e.g. "Chinese"),
# not ISO codes. Our CLI/skill document short codes like `zh`/`en`, so map them
# here; unknown values are passed through untouched for the library to validate.
_ISO_TO_NAME = {
    "zh": "Chinese", "zh-cn": "Chinese", "zh-hans": "Chinese",
    "yue": "Cantonese", "zh-yue": "Cantonese", "zh-hk": "Cantonese",
    "en": "English", "ar": "Arabic", "de": "German", "fr": "French",
    "es": "Spanish", "pt": "Portuguese", "id": "Indonesian", "it": "Italian",
    "ko": "Korean", "ru": "Russian", "th": "Thai", "vi": "Vietnamese",
    "ja": "Japanese", "tr": "Turkish", "hi": "Hindi", "ms": "Malay",
    "nl": "Dutch", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "pl": "Polish", "cs": "Czech", "fil": "Filipino", "tl": "Filipino",
    "fa": "Persian", "el": "Greek", "ro": "Romanian", "hu": "Hungarian",
    "mk": "Macedonian",
}


def _normalize_language(language: str | None) -> str | None:
    """Map an ISO code (`zh`) to the canonical name Qwen3-ASR expects (`Chinese`).

    None/empty → None (auto-detect). Unknown values pass through unchanged so the
    library raises its own (now surfaced) ``Unsupported language`` error.
    """
    if not language:
        return None
    return _ISO_TO_NAME.get(language.strip().lower(), language.strip())


def _get_model():
    """Lazy-load the Qwen3ASRModel singleton (heavy GPU model)."""
    global _model
    if _model is None:
        # vLLM uses NCCL even for single-GPU; ensure spawn multiprocessing
        # and disable stats to reduce log noise.
        os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")

        import torch
        from qwen_asr import Qwen3ASRModel

        _model = Qwen3ASRModel.LLM(
            model=ASR_MODEL,
            gpu_memory_utilization=0.7,
            max_inference_batch_size=128,
            max_new_tokens=4096,
            forced_aligner=FORCED_ALIGNER,
            forced_aligner_kwargs=dict(
                dtype=torch.bfloat16,
                device_map="cuda:0",
            ),
        )
    return _model


def transcribe(audio_path: str | Path, language: str | None = None) -> str:
    """Transcribe a single audio file. Returns plain text.

    Args:
        audio_path: Path to an audio file (mp3/m4a/wav/...).
        language: Optional hint like 'zh', 'en'. Omit for auto-detect.

    Returns:
        The transcribed text.
    """
    model = _get_model()
    lang = _normalize_language(language)
    results = model.transcribe(
        audio=[str(audio_path)],
        language=[lang] if lang else None,
    )
    return results[0].text if results else ""


# ═══════════════════════════════════════════════════════════════════════════
# Graceful shutdown — vLLM's NCCL process group hangs on exit if not
# explicitly torn down before the interpreter finalises.
# ═══════════════════════════════════════════════════════════════════════════

def is_loaded() -> bool:
    """True if the ASR model is currently resident in GPU memory."""
    return _model is not None


def unload() -> bool:
    """Tear down the ASR model and free GPU memory. Returns True if a model was freed.

    Call this when transcription work is complete (e.g. after a batch of videos).
    vLLM runs its engine in a *subprocess*; just dropping the Python handle can
    leave that subprocess alive holding GPU memory (and hanging the parent on
    exit). So we explicitly shut the engine core down first, then drop references
    and empty the CUDA cache.
    """
    global _model
    if _model is None:
        return False

    model, _model = _model, None

    # vllm.LLM lives on the qwen wrapper's `.model` attr (backend == "vllm").
    # v1 path: LLM.llm_engine.engine_core.shutdown() stops the engine subprocess.
    try:
        engine = getattr(getattr(model, "model", None), "llm_engine", None)
        core = getattr(engine, "engine_core", None)
        if core is not None and hasattr(core, "shutdown"):
            core.shutdown()
    except Exception:
        pass

    try:
        del model
    except Exception:
        pass

    # Reclaim the freed memory so a later reload (or other GPU job) sees it.
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return True


# Backwards-compatible alias used by the atexit / signal handlers below.
def shutdown():
    """Explicitly tear down the ASR model so vLLM cleans up its engine."""
    unload()


def _signal_handler(signum, frame):
    """Forward SIGTERM/SIGINT to vLLM cleanup, then exit cleanly.

    Calls shutdown() to delete the model (triggers vLLM engine-core
    teardown), then exits via os._exit to skip the NCCL ProcessGroup
    destructor which can hang during interpreter finalisation.
    A brief sleep gives the engine-core subprocess time to receive
    the shutdown signal before we bypass normal exit.
    """
    import time
    shutdown()
    time.sleep(0.5)
    os._exit(0 if signum != signal.SIGINT else 130)


# Register cleanup handlers.  atexit fires during normal exit;
# signal handlers catch external termination.
atexit.register(shutdown)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)
