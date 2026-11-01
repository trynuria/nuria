"""Application browser test runner. Invoked inside an offline bubblewrap namespace.

Only the candidate, a private result directory and installed browser/runtime are
mounted. No provider state, host services or other projects are visible.
"""

import hashlib
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

FILES = {
    "index.html",
    "style.css",
    "app.js",
    "engine.js",
    "toolkit.js",
    "toolkit-bridge.js",
    "inputs.json",
    "contract.json",
}
CSP = "sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'"


def observation(page, selector):
    element = page.locator(selector)
    if element.count() != 1 or not element.is_visible():
        raise ValueError(
            "Result selector must identify one visible region: " + selector
        )
    return element.evaluate(
        """el => ({text:el.innerText?.slice(0,5000)||'',html:el.outerHTML.slice(0,18000),canvas:[...el.querySelectorAll('canvas'),...(el.tagName==='CANVAS'?[el]:[])].map(c=>{try{return c.toDataURL()}catch{return 'unreadable'}})})"""
    )


def main():
    contract = json.loads(Path("/workspace/contract.json").read_text())
    journey = contract["journey"]
    output = Path("/results")
    base = "http://nuria.invalid"
    report = {
        "passed": False,
        "runner": "playwright-chromium",
        "network": "offline OS namespace",
        "views": [],
        "errors": [],
        "blocked_requests": [],
        "downloads": [],
    }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                chromium_sandbox=False,
                args=[
                    "--disable-dev-shm-usage",
                    "--renderer-process-limit=2",
                    "--no-zygote",
                ],
            )
            for label, width, height in (("desktop", 1280, 800), ("mobile", 390, 844)):
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    accept_downloads=True,
                    service_workers="block",
                )

                def route(request):
                    name = urlsplit(request.request.url).path.lstrip("/")
                    if request.request.url.startswith(base + "/"):
                        if name == "favicon.ico":
                            request.fulfill(status=204, body="")
                        elif name in FILES and (Path("/workspace") / name).is_file():
                            request.fulfill(
                                path=str(Path("/workspace") / name),
                                content_type=mimetypes.guess_type(name)[0]
                                or "application/json",
                                headers={"Content-Security-Policy": CSP},
                            )
                        else:
                            report["errors"].append(
                                "Missing local resource: " + name[:160]
                            )
                            request.fulfill(status=404, body="Missing local file")
                    else:
                        report["blocked_requests"].append(request.request.url[:200])
                        request.abort()

                context.route("**/*", route)
                page = context.new_page()
                page.set_default_timeout(2500)
                page.on(
                    "pageerror", lambda error: report["errors"].append(str(error)[:700])
                )
                page.on(
                    "console",
                    lambda message: (
                        report["errors"].append(message.text[:700])
                        if message.type == "error" and "favicon.ico" not in message.text
                        else None
                    ),
                )
                page.goto(base + "/index.html", wait_until="load", timeout=10000)
                page.wait_for_timeout(180)
                before = observation(page, journey["result"])
                actions = []
                for step in journey["steps"]:
                    target = page.locator(step["selector"])
                    if target.count() != 1 or not target.is_visible():
                        raise ValueError(
                            "Action needs one visible control: " + step["selector"]
                        )
                    action = step["action"]
                    if action == "click":
                        target.click()
                    elif action == "fill":
                        target.fill(str(step.get("value", "")))
                    elif action == "select":
                        target.select_option(str(step.get("value", "")))
                    elif action == "press":
                        target.press(str(step.get("value", "Enter")))
                    else:
                        raise ValueError("Unsupported action")
                    page.wait_for_timeout(100)
                    actions.append(
                        {
                            "action": action,
                            "selector": step["selector"],
                            "completed": True,
                        }
                    )
                after = observation(page, journey["result"])
                changed = before != after
                layout = page.evaluate(
                    """() => ({width:innerWidth,documentWidth:document.documentElement.scrollWidth,text:document.body.innerText.slice(0,7000),controls:document.querySelectorAll('button,input,select,textarea').length,title:document.title})"""
                )
                overflow = layout["documentWidth"] > width + 2
                if journey.get("download") and label == "desktop":
                    with page.expect_download(timeout=5000) as pending:
                        page.locator(journey["download"]).click()
                    download = pending.value
                    path = Path(download.path())
                    size = path.stat().st_size
                    if not 16 <= size <= 2000000:
                        raise ValueError("Export must contain 16 bytes to 2 MB")
                    report["downloads"].append(
                        {
                            "name": download.suggested_filename[:100],
                            "bytes": size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                    )
                screenshot = "preview.jpg" if label == "desktop" else "mobile.jpg"
                page.screenshot(path=str(output / screenshot), type="jpeg", quality=72)
                report["views"].append(
                    {
                        "viewport": label,
                        "passed": changed and not overflow and layout["controls"] >= 1,
                        "interaction_changed_result": changed,
                        "horizontal_overflow": overflow,
                        "actions": actions,
                        "before": before["text"][:1500],
                        "after": after["text"][:3500],
                        "visible_text": layout["text"],
                        "screenshot": screenshot,
                    }
                )
                context.close()
            browser.close()
        report["passed"] = (
            len(report["views"]) == 2
            and all(v["passed"] for v in report["views"])
            and not report["errors"]
            and not report["blocked_requests"]
        )
    except Exception as error:
        report["errors"].append(str(error)[:1000])
    report["errors"] = list(dict.fromkeys(report["errors"]))[:12]
    report["blocked_requests"] = list(dict.fromkeys(report["blocked_requests"]))[:12]
    (output / "browser.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
