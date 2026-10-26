"""Reject private credentials, local identities and conversation annotations."""

import re
import subprocess
from pathlib import Path

RULES = (
    ("local identity path", r"/Users/[^/\s]+/|[A-Z]:\\\\Users\\\\"),
    (
        "conversation annotation",
        r"(?i)\b(?:the user (?:asked|requested|instructed)|owner(?:['’]s)? request|in this chat|conversation_id|thread_id)\b",
    ),
    ("credential", r"\b(?:privy_app_secret_|fapi_|jup_)[A-Za-z0-9_-]{24,}"),
    (
        "OpenAI credential",
        r"\bsk-(?:(?:proj|svcacct)-[A-Za-z0-9_-]{32,}|[A-Za-z0-9]{32,})(?![A-Za-z0-9_-])",
    ),
    ("bot credential", r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}"),
    (
        "private key",
        r"-----BEGIN (?:EC |RSA |OPENSSH )?PRIVATE KEY-----[\s\S]{32,}?-----END",
    ),
)


def check(root):
    names = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    failures = []
    checked = 0
    for name in filter(None, names):
        path = root / name
        if name == "scripts/check_publication.py" or not path.is_file():
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            continue
        checked += 1
        for label, pattern in RULES:
            if re.search(pattern, content):
                failures.append({"path": name, "rule": label})
    if failures:
        raise ValueError(str(failures))  # Never echo a matched credential or text.
    return {
        "checked_text_files": checked,
        "private_material_detected": False,
        "scope": "Current publication tree; not a historical secret audit",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(check(Path(__file__).resolve().parents[1])))
