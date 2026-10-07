import json
import os
import tempfile
from pathlib import Path


def publish(directory, name, value):
    directory = Path(directory)
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    fd, temp = tempfile.mkstemp(prefix="." + name + "-", dir=directory)
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
    os.chmod(temp, 0o640)
    os.replace(temp, directory / name)
