"""Read-only Studio and sandboxed creations; no inference or write endpoints."""

import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ARTIFACT_CSP = "sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"


def handler(public, web):
    public, web = Path(public).resolve(), Path(web).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            route = urlsplit(self.path).path
            creation = False
            if route == "/api/studio":
                path = public / "status.json"
            elif route in ("/studio", "/studio/", "/studio/index.html"):
                path = web / "index.html"
            elif route in ("/studio/studio.css", "/studio/studio.js"):
                path = web / route.rsplit("/", 1)[1]
            elif route.startswith("/creations/"):
                creation = True
                pieces = route.strip("/").split("/")
                import re

                if (
                    len(pieces) != 4
                    or not re.fullmatch(r"invention-[0-9]{3}", pieces[1])
                    or pieces[2] not in ("a", "b")
                    or pieces[3]
                    not in (
                        "index.html",
                        "style.css",
                        "app.js",
                        "engine.js",
                        "toolkit.js",
                        "toolkit-bridge.js",
                        "inputs.json",
                        "manifest.json",
                    )
                ):
                    return self.send_error(404)
                path = public.joinpath(*pieces)
                if (
                    not path.resolve().is_relative_to(public / "creations")
                    or path.is_symlink()
                ):
                    return self.send_error(404)
            else:
                return self.send_error(404)
            try:
                raw = path.read_bytes()
                if len(raw) > 2000000:
                    return self.send_error(503)
            except OSError:
                return self.send_error(404)
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "Cache-Control",
                "no-cache" if route == "/api/studio" else "public, max-age=30",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                ARTIFACT_CSP
                if creation
                else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("X-Frame-Options", "SAMEORIGIN" if creation else "DENY")
            self.end_headers()
            try:
                if self.command != "HEAD":
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", required=True)
    parser.add_argument("--web", default=str(Path(__file__).parent / "web"))
    parser.add_argument("--port", type=int, default=3042)
    args = parser.parse_args()
    ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler(args.public, args.web)
    ).serve_forever()
