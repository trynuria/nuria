"""Read-only Studio and sandboxed creations; no inference or write endpoints."""

import argparse
import gzip
import hashlib
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ARTIFACT_CSP = "sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"


def version_assets(raw, path, route):
    """Fresh HTML must not reuse a CDN's earlier script or stylesheet revision."""

    def replace(match):
        value = match[3].decode("utf-8")
        try:
            url = urlsplit(value)
        except ValueError:
            return match[0]
        if url.scheme or url.netloc:
            return match[0]
        name = url.path
        prefix = route.rsplit("/", 1)[0] + "/"
        if name.startswith(prefix):
            name = name[len(prefix) :]
        if name.startswith("./"):
            name = name[2:]
        if name not in {
            "app.js",
            "engine.js",
            "style.css",
            "toolkit.js",
            "toolkit-bridge.js",
            "studio.js",
            "studio.css",
        }:
            return match[0]
        asset = path.parent / name
        if asset.is_symlink() or not asset.is_file():
            return match[0]
        version = hashlib.sha256(asset.read_bytes()).hexdigest()
        # Generated files remain byte-for-byte intact in the source archive.
        # Existing query/fragment values have no role in these static assets.
        value = (url.path + "?v=" + version).encode()
        return match[1] + match[2] + value + match[2]

    return re.sub(
        rb"""(<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*)(["'])([^"']+)\2""",
        replace,
        raw,
        flags=re.IGNORECASE,
    )


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
                        "contract.json",
                        "preview.jpg",
                        "mobile.jpg",
                        "browser.json",
                        "challenge.json",
                        "outcome-baseline.json",
                        "outcome-result.json",
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
                if len(raw) > (16000000 if route == "/api/studio" else 2000000):
                    return self.send_error(503)
            except OSError:
                return self.send_error(404)
            if path.suffix == ".html":
                raw = version_assets(
                    raw, path, route if creation else "/studio/index.html"
                )
            self.send_response(200)
            if route == "/api/studio":
                self.send_header("Vary", "Accept-Encoding")
                if "gzip" in self.headers.get("Accept-Encoding", "").lower():
                    raw = gzip.compress(raw, compresslevel=3)
                    self.send_header("Content-Encoding", "gzip")
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(raw)))
            self.send_header(
                "Cache-Control",
                "no-store" if creation else "no-cache",
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
