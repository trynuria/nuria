"""Download all public payment records and verify one advertised snapshot."""

import argparse
import json
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from commerce.evidence import verify_pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="https://nuria.network")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = urlsplit(args.origin)
    if (
        url.scheme != "https"
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise ValueError("Use a public credential-free HTTPS origin")

    def get(path):
        request = urllib.request.Request(
            args.origin.rstrip("/") + path,
            headers={
                "User-Agent": "Nuria-proof-verifier/0.6 (+https://github.com/trynuria/nuria)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("Ledger page is oversized")
            return json.loads(raw)

    index = get("/api/commerce/index")
    if index["pages"] > 1_000_000:
        raise ValueError("Implausible ledger size")
    args.output.mkdir(parents=True, exist_ok=False)

    def pages():
        for page in range(index["pages"]):
            time.sleep(0.12)  # Stay below the public origin's per-client API ceiling.
            body = get(f"/api/commerce/ledger?page={page}")
            (args.output / f"ledger-{page}.json").write_text(
                json.dumps(body, indent=2) + "\n"
            )
            yield body

    result = verify_pages(index, pages())
    (args.output / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
