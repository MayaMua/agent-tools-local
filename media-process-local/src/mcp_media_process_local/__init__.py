"""
Media Process Local — MCP server + CLI tools for local media processing.

Components:
  - downloader: yt-dlp wrapper (audio/video/subtitle download from 1000+ sites)
  - asr:        Qwen3-ASR local GPU transcription
  - pipeline:   Full download-and-transcribe orchestration
  - server:     FastMCP server exposing the pipeline as structured MCP tools
"""

__version__ = "0.1.0"
