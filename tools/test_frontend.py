from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception as exc:
        import glob
        import os
        roots = [
            os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"),
            os.path.expanduser("~/.cache/ms-playwright"),
        ]
        found = []
        for root in roots:
            found += glob.glob(os.path.join(root, "chromium-*", "chrome-win64", "chrome.exe"))
            found += glob.glob(os.path.join(root, "chromium-*", "chrome-linux", "chrome"))
        if not found:
            raise
        exe = sorted(found)[-1]
        print(f"(default launch failed: {str(exc)[:60]}…)")
        print(f"(falling back to {exe})")
        return p.chromium.launch(executable_path=exe)


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "serve.py"), "--dir", "web", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    failures = []
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page()
            errors = []
            ranged = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: ranged.append(r.url)
                    if r.headers.get("range") else None)

            page.goto(BASE, wait_until="domcontentloaded")
            page.wait_for_function(
                "document.getElementById('status').textContent.startsWith('Results')",
                timeout=30000,
            )

            status = page.inner_text("#status")
            cards = page.locator(".card").count()
            print(f"initial load     : {status!r}, {cards} cards")
            print(f"range requests   : {len(ranged)}")
            if cards == 0:
                failures.append("no cards rendered on initial load")
            if ranged:
                failures.append(f"initial paint should need no range requests, saw {len(ranged)}")

            page.fill("#q", "asphalt")
            page.click("#filters button[type=submit]")
            page.wait_for_timeout(1200)
            status = page.inner_text("#status")
            cards = page.locator(".card").count()
            print(f"search 'asphalt' : {status!r}, {cards} cards")
            if cards == 0:
                failures.append("search 'asphalt' returned no cards")
            if "q=asphalt" not in page.url:
                failures.append(f"search not reflected in URL: {page.url}")
            if not ranged:
                failures.append("no Range requests after search (httpvfs not working)")

            page.fill("#q", "")
            page.fill("#pkg", "com.gameloft")
            page.click("#filters button[type=submit]")
            page.wait_for_timeout(1200)
            cards = page.locator(".card").count()
            print(f"package prefix   : {page.inner_text('#status')!r}, {cards} cards")
            if cards == 0:
                failures.append("package prefix search returned no cards")

            show = page.locator("a.more").first
            if show.count() == 0:
                failures.append("no 'Show all' link present")
            else:
                show.click()
                page.wait_for_timeout(1200)
                back = page.locator("#status a")
                print(f"show all         : {page.inner_text('#status')!r}, "
                      f"{page.locator('.card').count()} cards")
                if back.count() == 0:
                    failures.append("'previous search' backlink missing")
                else:
                    dl = page.locator(".card a[href*='archive.org/download']").first
                    if dl.count() == 0:
                        failures.append("no archive.org download link in detail view")
                    else:
                        print(f"download link    : {dl.get_attribute('href')[:78]}")
                    back.first.click()
                    page.wait_for_timeout(1000)
                    if "tid=" in page.url:
                        failures.append("backlink did not clear gid from URL")

            page.click("#random")
            page.wait_for_timeout(1500)
            print(f"random           : {page.inner_text('#status')!r}, "
                  f"{page.locator('.card').count()} cards")
            if "tid=" not in page.url:
                failures.append("Random did not navigate to a package")

            real = [e for e in errors if "favicon" not in e.lower()]
            if real:
                failures.append(f"console errors: {real[:3]}")

            browser.close()
    finally:
        server.terminate()

    print()
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("all frontend checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
