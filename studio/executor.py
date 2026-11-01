"""Offline artifact validation in bubblewrap, plus a narrow public file format."""

import hashlib
import json
import re
import resource
import shutil
import subprocess
from pathlib import Path

ALLOWED = {"index.html", "style.css", "app.js", "engine.js"}
MAX_FILES_BYTES = 160000
BRIDGE = "globalThis.NuriaToolkit = Object.freeze(globalThis.NuriaEngine);\n"


def retained_engine(directory):
    """Keep the dependency closure and bind each generation to its own parent."""
    directory = Path(directory)
    code = (directory / "engine.js").read_text()
    parent = directory / "toolkit.js"
    if not parent.is_file():
        return code
    return (
        parent.read_text()
        + "\n(function(root) {\n"
        + "const parent = root.NuriaEngine; const scope = Object.create(root);\n"
        + "scope.NuriaToolkit = parent;\n"
        + "root.NuriaEngine = (function(globalThis, NuriaToolkit) {\n"
        + code
        + "\nreturn globalThis.NuriaEngine;\n})(scope, parent);\n})(globalThis);\n"
    )


def write_files(directory, files):
    if type(files) is not dict or not files or set(files) - ALLOWED:
        raise ValueError("Write only index.html, style.css, app.js or engine.js")
    if (
        any(type(v) is not str for v in files.values())
        or sum(len(v.encode()) for v in files.values()) > MAX_FILES_BYTES
    ):
        raise ValueError("Artifact exceeds its source allowance")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        if re.search(
            r"-----BEGIN .*PRIVATE KEY|\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}", body
        ):
            raise ValueError("Credential-like material is not a public artifact")
        (directory / name).write_text(body)


def limits():
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def check(directory, node):
    directory = Path(directory).resolve()
    missing = ALLOWED - {p.name for p in directory.iterdir() if p.is_file()}
    if missing:
        return {"passed": False, "details": ["Missing: " + ", ".join(sorted(missing))]}
    html = (directory / "index.html").read_text()
    if (
        "<title" not in html.lower()
        or "engine.js" not in html
        or "app.js" not in html
        or len(re.findall(r"<(?:button|input|select)\b", html, re.I)) < 2
    ):
        return {
            "passed": False,
            "details": [
                "Provide a titled page, engine.js, app.js and at least two interactive controls"
            ],
        }
    if re.search(
        r"<(?:iframe|object|embed|base|form)\b|(?:src|href)\s*=\s*[\"\']\s*(?:https?:|//|javascript:)",
        html,
        re.I,
    ):
        return {
            "passed": False,
            "details": ["Artifacts must be self-contained and offline"],
        }
    sandbox = shutil.which("bwrap")
    if not sandbox:
        raise RuntimeError("bubblewrap is required; no unisolated fallback")
    command = [
        sandbox,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
    ]
    for root in ("/usr", "/lib", "/lib64"):
        if Path(root).exists():
            command += ["--ro-bind", root, root]
    checker = Path(__file__).with_name("check_engine.cjs").resolve()
    command += [
        "--dir",
        "/runtime",
        "--ro-bind",
        str(Path(node).resolve()),
        "/runtime/node",
        "--ro-bind",
        str(directory),
        "/workspace",
        "--ro-bind",
        str(checker),
        "/check.cjs",
        "--tmpfs",
        "/tmp",
        "--dev",
        "/dev",
        "--chdir",
        "/workspace",
        "/runtime/node",
        "--max-old-space-size=128",
        "/check.cjs",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            preexec_fn=limits,
            env={"PATH": "/usr/bin:/bin"},
        )
        lines = result.stdout.strip().splitlines()
        value = json.loads(lines[-1]) if lines else None
        if type(value) is not dict or type(value.get("passed")) is not bool:
            raise ValueError("No checker result")
        value["passed"] = value["passed"] and result.returncode == 0
        value["engine_sha256"] = hashlib.sha256(
            (directory / "engine.js").read_bytes()
        ).hexdigest()
        return value
    except (ValueError, subprocess.TimeoutExpired):
        return {
            "passed": False,
            "details": ["Engine could not complete the isolated host checks"],
        }


def publish(directory, destination):
    directory, destination = Path(directory), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for folder in (destination, destination.parent, destination.parent.parent):
        folder.chmod(0o755)
    manifest = {}
    for name in sorted(ALLOWED | {"toolkit.js", "toolkit-bridge.js", "inputs.json"}):
        path = directory / name
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            (destination / name).write_bytes(data)
            (destination / name).chmod(0o644)
            manifest[name] = hashlib.sha256(data).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (destination / "manifest.json").chmod(0o644)
    return manifest
