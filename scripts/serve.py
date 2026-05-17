"""Static dev server for the docs/ folder.

Respects the PORT env var (defaults to 8000). Used by .claude/launch.json
for the preview pane; also handy from a shell:

    PORT=8765 python scripts/serve.py
"""

from __future__ import annotations

import http.server
import os
import socketserver
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

    def end_headers(self):
        # Disable caching so refresh.py changes show up immediately.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        super().end_headers()


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), _Handler) as httpd:
        print(f"FantasyGM dev server: http://127.0.0.1:{port}  (docs/={DOCS_DIR})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
