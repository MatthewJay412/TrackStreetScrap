"""
ONE-TIME DIAGNOSTIC -- not part of the regular pipeline.

Watches real network activity (API calls only, not images/styling/noise)
around clicking "Export as CSV" for Elkay Inc., to see what actually
happens on the first click vs. the second. Prints a plain timestamped log
you can read yourself and paste back.

Run manually:
    python3 debug_export_timing.py
"""

import time

from playwright.sync_api import sync_playwright

import config

launch_kwargs = {"headless": False}
if config.BROWSER_CHANNEL:
    launch_kwargs["channel"] = config.BROWSER_CHANNEL

config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def log_request(request):
    if "trackstreet.com" in request.url:
        print(f"[{time.strftime('%H:%M:%S')}] -> {request.method} {request.url}")


def log_response(response):
    if "trackstreet.com" in response.url:
        print(f"[{time.strftime('%H:%M:%S')}] <- {response.status} {response.url}")


def log_download(download):
    print(f"[{time.strftime('%H:%M:%S')}] *** REAL DOWNLOAD EVENT: {download.suggested_filename} ***")


with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(str(config.PROFILE_DIR), **launch_kwargs)
    page = context.pages[0] if context.pages else context.new_page()
    page.on("request", log_request)
    page.on("response", log_response)
    page.on("download", log_download)

    page.goto(config.TRACKSTREET_URL)
    try:
        page.fill("#email", config.TRACKSTREET_EMAIL, timeout=5000)
        page.fill("#password", config.TRACKSTREET_PASSWORD, timeout=5000)
        page.press("#password", "Enter")
    except Exception:
        print("Already logged in, continuing.")

    page.get_by_role("button", name="Reporting").wait_for(timeout=30000)
    page.get_by_role("button", name="Reporting").click()
    page.get_by_role("button", name="Market Reports", exact=True).click()
    page.get_by_title("All Crawls").click()
    page.get_by_role("button", name="Filters").click()
    page.get_by_role("button", name="Time Window", exact=True).click()
    page.get_by_label("Last Crawl").get_by_text("Last Crawl").click()

    print("\n" + "=" * 60)
    print("CLICKING EXPORT AS CSV NOW (1st time) -- watch the log below")
    print("=" * 60 + "\n")
    page.locator(".relative.flex > div:nth-child(2)").first.click()
    page.get_by_role("menuitem", name="Export as CSV").click()

    print("\n--- watching for 40 seconds ---\n")
    page.wait_for_timeout(40000)

    print("\n" + "=" * 60)
    print("CLICKING EXPORT AS CSV AGAIN (2nd time, the 'retry' click)")
    print("=" * 60 + "\n")
    page.locator(".relative.flex > div:nth-child(2)").first.click()
    page.get_by_role("menuitem", name="Export as CSV").click()

    print("\n--- watching for 15 more seconds ---\n")
    page.wait_for_timeout(15000)

    print("\nDone. Copy everything above (from the first === line down) and send it back.")
    context.close()
