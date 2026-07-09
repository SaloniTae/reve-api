# interactive_browser.py
# Launches a real (non-headless) Chromium browser with stealth patches applied,
# meant to be run with DISPLAY=:99 so it shows up in your noVNC session.
# You then drive it manually via touch/mouse on your phone.

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync
import time


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--start-maximized",
                "--window-position=0,0",
            ],
            ignore_default_args=["--no-startup-window"],
        )

        context = browser.new_context(viewport=None)  # instead of no_viewport=True
        page = context.new_page()

        stealth_sync(page)

        # Optional: log every network response so you can watch requests live
        def handle_response(response):
            try:
                print(f"[NET] {response.status} {response.url[:100]}", flush=True)
            except Exception:
                pass

        page.on("response", handle_response)

        print("Browser launched. Navigate manually from your VNC session.")
        page.goto("https://app.reve.com")  # starting point; change/remove as you like

        # Keep the script alive so the browser stays open for interactive use
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Closing browser...")
            browser.close()


if __name__ == "__main__":
    main()
