"""python -m mcp_media_process_local — subcommand router.

Usage:
  python -m mcp_media_process_local server          # Start MCP server (stdio)
  python -m mcp_media_process_local pipeline <url>   # Full download+transcribe
  python -m mcp_media_process_local dl <url>         # Download only
"""

import sys


def _usage() -> None:
    print(__doc__, file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 2:
        _usage()

    cmd = sys.argv[1]
    # Remove the sub-command so argparse in sub-modules works normally.
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if cmd == "server":
        from mcp_media_process_local.server import main as server_main
        server_main()
    elif cmd == "pipeline":
        from mcp_media_process_local.pipeline import main as pipeline_main
        pipeline_main()
    elif cmd == "dl":
        from mcp_media_process_local.downloader import main as dl_main
        dl_main()
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        _usage()


if __name__ == "__main__":
    main()
