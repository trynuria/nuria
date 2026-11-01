"""Persistent workspaces inside Nuria's enrolled offline Linux boundary."""

import hashlib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PUBLIC_HOSTS = frozenset(
    {
        "api.github.com",
        "raw.githubusercontent.com",
        "github.com",
        "nuria.network",
        "docs.python.org",
        "developer.mozilla.org",
        "sqlite.org",
        "www.sqlite.org",
        "pypi.org",
        "www.rfc-editor.org",
        "numpy.org",
        "pandas.pydata.org",
        "docs.scipy.org",
        "doc.rust-lang.org",
        "docs.rs",
    }
)


def public_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Public URL exceeds its bound")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in PUBLIC_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Read-only public-source host is not enrolled")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, public_url(newurl))


def fetch_public(url):
    url = public_url(url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), PublicRedirect()
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Nuria-research/1.0",
            "Accept": "application/json,text/plain,text/html",
        },
    )
    try:
        response = opener.open(request, timeout=12)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        return {
            "url": url,
            "status": code,
            "error": "Public source refused this read; no access controls were changed",
        }
    with response:
        content_type = response.headers.get_content_type()
        if not (
            content_type.startswith("text/")
            or content_type in ("application/json", "application/vnd.github+json")
        ):
            raise ValueError("Only public text or JSON sources are enrolled")
        raw = response.read(262145)
        return {
            "url": url,
            "status": response.status,
            "sha256": hashlib.sha256(raw[:262144]).hexdigest(),
            "truncated": len(raw) > 262144,
            "text": raw[:262144].decode("utf-8", errors="replace"),
        }


def files_under(root, maximum=33554432):
    """Never follow a generated symlink while inspecting or publishing files."""
    root = Path(root).resolve()
    found = {}
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Workspace symlinks cannot cross the artifact boundary")
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root) or path.stat().st_nlink != 1:
            raise ValueError("Workspace link escapes the artifact boundary")
        size = path.stat().st_size
        total += size
        if size > 8388608 or total > maximum or len(found) >= 1000:
            raise ValueError("Workspace exceeds its file or byte allowance")
        found[str(path.relative_to(root))] = path
    return found


class ShellWorkspace:
    def __init__(
        self, root, source, library, *, bwrap=None, dependencies=(), node=None
    ):
        self.root = Path(root).resolve()
        self.work = self.root / "work"
        self.observations = self.root / "observations"
        for path in (self.work, self.observations, self.root / "executions"):
            path.mkdir(parents=True, exist_ok=True)
        self.source = Path(source).resolve()
        self.library = Path(library).resolve()
        self.library.mkdir(parents=True, exist_ok=True)
        self.bwrap = bwrap or shutil.which("bwrap")
        if not self.bwrap:
            raise ValueError("An enrolled offline bubblewrap backend is required")
        self.dependencies = [str(Path(p).resolve()) for p in dependencies]
        self.node = node

    def command(self, command, *, writable=False, checker=False):
        args = [
            self.bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--clearenv",
        ]
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        for path in ("/etc/ld.so.cache", "/etc/localtime"):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        for path in self.dependencies:
            args.extend(["--ro-bind", path, path])
        if self.node:
            args.extend(["--ro-bind", self.node, self.node])
        args.extend(
            [
                "--ro-bind",
                str(self.source),
                "/repo",
                "--ro-bind",
                str(self.library),
                "/library",
                "--ro-bind",
                str(self.observations),
                "/observations",
                "--bind" if writable else "--ro-bind",
                str(self.work),
                "/work",
                "--tmpfs",
                "/tmp",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--setenv",
                "PATH",
                (self.node + "/bin:" if self.node else "") + "/usr/bin:/bin",
                "--setenv",
                "PYTHONPATH",
                ":".join(self.dependencies),
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--setenv",
                "LC_ALL",
                "C.UTF-8",
                "--setenv",
                "NODE_OPTIONS",
                "--max-old-space-size=192",
                "--chdir",
                "/work",
            ]
        )
        if checker:
            return args + [
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                "import sys; exec(compile(sys.stdin.read(), '<frozen-acceptance>', 'exec'))",
            ]
        return args + ["/bin/bash", "--noprofile", "--norc", "-c", command]

    def execute(self, command, label, *, writable=False, checker=False):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", label):
            raise ValueError("Execution label must be a bounded identifier")
        if not isinstance(command, str) or len(command.encode()) > 30000:
            raise ValueError("One bounded command or checker is required")
        files_under(self.work)
        directory = self.root / "executions" / label
        directory.mkdir()

        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
            # Acceptance fixtures live in the disposable /tmp mount. Allow
            # realistic streaming inputs while keeping model work files small.
            file_limit = 134217728 if checker else 8388608
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

        started = time.monotonic()
        with (
            (directory / "stdout.txt").open("wb") as out,
            (directory / "stderr.txt").open("wb") as err,
        ):
            process = subprocess.Popen(
                self.command(command, writable=writable, checker=checker),
                stdin=subprocess.PIPE if checker else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
                preexec_fn=limits,
            )
            timed_out = False
            try:
                process.communicate(command.encode() if checker else None, timeout=25)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        result = {
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "seconds": round(time.monotonic() - started, 6),
        }
        for kind in ("stdout", "stderr"):
            raw = (directory / f"{kind}.txt").read_bytes()
            result[kind] = raw[:6000].decode(errors="replace")
            result[kind + "_sha256"] = hashlib.sha256(raw).hexdigest()
            result[kind + "_truncated"] = len(raw) > 6000
            # Full tool output remains available to the agent on its next step.
            (self.observations / f"{label}-{kind}.txt").write_bytes(raw)
        result["full_output"] = f"/observations/{label}-stdout.txt"
        try:
            files_under(self.work)
        except ValueError as error:
            result.update(exit_code=125, workspace_error=str(error))
        (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
