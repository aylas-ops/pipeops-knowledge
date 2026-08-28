"""A minimal localhost retrieval endpoint, so an agent can call the knowledge
base as a tool.

Pilot-grade on purpose: stdlib `http.server`, bound to loopback, no auth, no
concurrency story. It exists to close the loop in week 3 of the pilot -- an
agent with a `search_knowledge` tool pointed here produces cited answers, and
that is the thing the pilot has to demonstrate.

Do not expose this beyond localhost. Production serving belongs behind the
same IAM and document ACLs as the source systems (manual §14: "OKF can expose
sensitive business context if permissions are not aligned").

Endpoints:
    GET /health
    GET /search?q=...&type=...&tag=...&owner=...&limit=...&format=json|context
    GET /concept/<bundle>/<path>
    GET /stats
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .index import stats as index_stats
from .query import format_context, get_concept, search

_TRUE = {"1", "true", "yes", "on"}


def _handler_class(db_path: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "okf-serve/0.1"

        def log_message(self, fmt: str, *args) -> None:  # quieter default logging
            print(f"{self.address_string()} {fmt % args}")

        def _send(self, payload: object, status: int = 200, content_type: str = "application/json") -> None:
            body = (
                payload.encode("utf-8")
                if isinstance(payload, str)
                else json.dumps(payload, indent=2, default=str).encode("utf-8")
            )
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            route = parsed.path.rstrip("/") or "/"

            if route == "/health":
                self._send({"ok": True, "db": str(db_path)})
                return

            if route == "/stats":
                self._send(index_stats(db_path))
                return

            if route == "/search":
                query = (params.get("q") or [""])[0]
                if not query.strip():
                    self._send({"error": "missing `q`"}, status=400)
                    return
                try:
                    results = search(
                        db_path,
                        query,
                        types=params.get("type"),
                        tags=params.get("tag"),
                        owner=(params.get("owner") or [None])[0],
                        bundle=(params.get("bundle") or [None])[0],
                        include_drafts=(params.get("include_drafts") or ["0"])[0] in _TRUE,
                        include_stale=(params.get("include_stale") or ["0"])[0] in _TRUE,
                        limit=int((params.get("limit") or ["8"])[0]),
                    )
                except (ValueError, RuntimeError) as exc:
                    self._send({"error": str(exc)}, status=400)
                    return

                if (params.get("format") or ["json"])[0] == "context":
                    self._send(format_context(results), content_type="text/plain")
                else:
                    self._send(
                        {
                            "query": query,
                            "count": len(results),
                            "results": [
                                {**asdict(result), "citation": result.citation,
                                 "warnings": result.warnings}
                                for result in results
                            ],
                        }
                    )
                return

            if route.startswith("/concept/"):
                concept_id = urllib.parse.unquote(route[len("/concept/"):])
                record = get_concept(db_path, concept_id)
                if record is None:
                    self._send({"error": f"not found: {concept_id}"}, status=404)
                else:
                    self._send(record)
                return

            self._send({"error": f"no route {route}"}, status=404)

    return Handler


def run(db_path: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    if not Path(db_path).exists():
        raise SystemExit(f"error: no index at {db_path}. Run `okf index` first.")
    server = ThreadingHTTPServer((host, port), _handler_class(Path(db_path)))
    print(f"okf serve → http://{host}:{port}  (index: {db_path})")
    print("  GET /search?q=...&format=context   grounding block for an agent prompt")
    print("  GET /concept/<bundle>/<path>       one full concept")
    print("  GET /stats                         coverage and trust breakdown")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
