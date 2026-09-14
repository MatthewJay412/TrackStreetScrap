"""
Interactive helper for finding TrackStreet's real selectors.

Run this manually (never via Task Scheduler) to log in and then pause with
Playwright's Inspector open. From there, use the Inspector's "Pick locator"
tool and click on TrackStreet's brand switcher, each brand's export button,
and any element that only appears once logged in -- the Inspector shows you
the exact selector for whatever you click. Copy those into config.py.

Usage:
    python debug_explore.py
"""

from playwright.sync_api import sync_playwright

import config

launch_kwargs = {"headless": False}
if config.BROWSER_CHANNEL:
    launch_kwargs["channel"] = config.BROWSER_CHANNEL

# Same persistent profile folder as trackstreet_sync.py uses -- this is
# what makes a "verify this device" check completed here actually count
# for future runs of the real script too, instead of being thrown away
# the moment this window closes.
config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        str(config.PROFILE_DIR), **launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(config.TRACKSTREET_URL)
    try:
        # Short timeout: if the persistent profile already has a valid
        # session, these fields won't exist -- fail fast instead of hanging.
        page.fill("#email", config.TRACKSTREET_EMAIL, timeout=5000)
        page.fill("#password", config.TRACKSTREET_PASSWORD, timeout=5000)
        page.press("#password", "Enter")
    except Exception:
        print("Login fields not found -- likely already logged in from a saved session. Continuing.")

    print("\nBrowser is open and the Playwright Inspector should appear.")
    print("Use the Inspector's 'Pick locator' tool to click on:")
    print("  1. The brand switcher / brand picker")
    print("  2. Each brand's export or download button")
    print("  3. Something that ONLY appears once logged in (for login checks)")
    print("Copy the resulting selectors into config.py, then close the")
    print("Inspector window to end this script.\n")

    page.pause()
    context.close()
