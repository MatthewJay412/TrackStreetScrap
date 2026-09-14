"""
TrackStreet -> Tableau sync.

Logs into TrackStreet, downloads a per-brand pricing/violation export for
each configured brand, combines them into one file, and writes it
atomically to the fixed path Tableau refreshes from.

Run manually to test:
    python trackstreet_sync.py

Run for debugging (visible browser window):
    set HEADLESS=false in .env, then run manually and watch it work.

Designed to be triggered unattended via Windows Task Scheduler -- see
README.md for setup steps. Every meaningful step is logged to
logs/trackstreet_sync.log, and failures trigger an email alert (if
SMTP_HOST / ALERT_TO are set in .env) so a bad run doesn't fail silently.

Exit codes (visible in Task Scheduler's "Last Run Result" column):
    0 = full success, all brands exported and combined
    1 = hard failure (bad config, login failed, or all exports failed)
    2 = partial success -- file was written, but 1+ brands were skipped
"""

import atexit
import logging
import logging.handlers
import re
import smtplib
import sys
import time
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

import config

logger = logging.getLogger("trackstreet_sync")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return  # already configured -- avoid duplicate/stacked log lines if run() is ever called twice in-process

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_DIR / "trackstreet_sync.log", maxBytes=2_000_000, backupCount=5
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# Email alerting
# ---------------------------------------------------------------------------
def send_alert(subject: str, body: str):
    if not config.SMTP_HOST or not config.ALERT_TO:
        logger.warning("SMTP_HOST / ALERT_TO not configured -- skipping email alert.")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = f"[TrackStreet Sync] {subject}"
        msg["From"] = config.ALERT_FROM
        msg["To"] = ", ".join(config.ALERT_TO)

        # Port 465 is implicit SSL (connection is encrypted from the start);
        # anything else uses plain SMTP, optionally upgraded via STARTTLS.
        server_cls = smtplib.SMTP_SSL if config.SMTP_PORT == 465 else smtplib.SMTP
        with server_cls(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            if config.SMTP_USE_TLS and config.SMTP_PORT != 465:
                server.starttls()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.ALERT_FROM, config.ALERT_TO, msg.as_string())
        logger.info("Alert email sent: %s", subject)
    except Exception:
        # A broken mail server shouldn't crash the error-handling path itself.
        logger.error("Failed to send alert email:\n%s", traceback.format_exc())


def tail_log(n_lines: int = 40) -> str:
    log_file = config.LOG_DIR / "trackstreet_sync.log"
    if not log_file.exists():
        return "(no log file found)"
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n_lines:])


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------
def with_retries(fn, *args, what: str, **kwargs):
    last_exc = None
    for attempt in range(1, config.MAX_RETRIES + 2):  # +1 for the initial try
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            logger.warning("%s failed (attempt %d): %s", what, attempt, exc)
            if attempt <= config.MAX_RETRIES:
                time.sleep(config.RETRY_DELAY_SEC)
    raise last_exc


# ---------------------------------------------------------------------------
# Playwright steps
# ---------------------------------------------------------------------------
def login(page):
    logger.info("Navigating to TrackStreet login page")
    page.goto(config.TRACKSTREET_URL, timeout=config.NAV_TIMEOUT_MS)

    try:
        # Short timeout here on purpose: if the persistent profile already
        # has a valid session (common after the first successful run),
        # these fields won't exist at all -- fail fast instead of waiting
        # out the full NAV_TIMEOUT_MS for something that'll never appear.
        page.fill("#email", config.TRACKSTREET_EMAIL, timeout=5000)
        page.fill("#password", config.TRACKSTREET_PASSWORD, timeout=5000)
        # Pressing Enter submits most login forms, including typical Element
        # UI ones. If TrackStreet's doesn't respond to Enter, replace this
        # with a click on the real submit button, e.g.:
        #   page.click('button:has-text("Log In")')
        page.press("#password", "Enter")
    except Exception:
        logger.info("Login fields not found -- likely already logged in from a saved session")

    # "Reporting" only appears once actually logged in -- confirmed via
    # debug_explore.py, replaces the earlier placeholder.
    page.get_by_role("button", name="Reporting").wait_for(timeout=config.NAV_TIMEOUT_MS)
    logger.info("Login successful")


def navigate_to_reports(page):
    logger.info("Navigating to Market Reports > All Crawls")
    page.get_by_role("button", name="Reporting").click()
    page.get_by_role("button", name="Market Reports", exact=True).click()
    page.get_by_title("All Crawls").click()  # "All Crawls" is a report page, not a time filter

    logger.info("Setting time window to Last Crawl")
    page.get_by_role("button", name="Filters").click()
    page.get_by_role("button", name="Time Window", exact=True).click()
    page.get_by_label("Last Crawl").get_by_text("Last Crawl").click()
    # NOTE: this was already set to "Last Crawl" when captured, so it's
    # unconfirmed whether TrackStreet needs a separate "Apply"/"Save" click
    # after selecting it. Watch for this during the first real test run --
    # if the exported data doesn't reflect the filter, that's the likely
    # cause, and the fix is one more .click() added right here.


def _safe_filename(text: str) -> str:
    """Turn a brand name into something safe to use as a filename (brand
    names like 'Elkay Inc.' end in a period, which Windows dislikes)."""
    return re.sub(r'[<>:"/\\|?*]', "_", text).rstrip(". ")


def export_brand(page, brand_name: str) -> Path:
    logger.info("Switching to brand: %s", brand_name)
    # The button's own label changes to reflect whichever brand is
    # CURRENTLY selected ("Brand: Elkay Inc.", then later "Brand: World
    # Dryer", etc.) -- matching just the "Brand:" prefix means this one
    # selector keeps working no matter which brand came before it.
    page.get_by_role("button", name=re.compile(r"^Brand:")).click()
    page.get_by_role("option", name=brand_name, exact=True).click()

    def open_menu_and_click_export():
        # Opens the "..." menu above the results table. This selector is
        # positional, not name/role-based -- the button itself has no
        # accessible label (icon only). It's the LEAST confident selector
        # in this whole script; if something breaks after a TrackStreet UI
        # change, re-check this exact spot first with debug_explore.py.
        page.locator(".relative.flex > div:nth-child(2)").first.click()
        page.get_by_role("menuitem", name="Export as CSV").click()

    logger.info("Triggering export for brand: %s", brand_name)
    # Confirmed via testing: TrackStreet's export sometimes needs more than
    # one click to actually start a real download, and the exact number
    # isn't consistent from brand to brand (usually 2, sometimes more).
    # IMPORTANT: also observed a mid-run forced logout during testing,
    # coinciding with repeated clicks happening in quick succession -- very
    # plausibly TrackStreet's own security response to what looks like
    # automated/abusive traffic. So this waits patiently and generously
    # between attempts on purpose, rather than clicking as fast as possible.
    downloads = []
    handler = lambda d: downloads.append(d)
    page.on("download", handler)
    try:
        max_clicks = 4
        for click_num in range(1, max_clicks + 1):
            open_menu_and_click_export()
            for _ in range(10):  # poll every 1s, up to 10s per click
                if downloads:
                    break
                page.wait_for_timeout(1000)
            if downloads:
                logger.info("Download started after %d click(s) for %s", click_num, brand_name)
                break
            if click_num < max_clicks:
                page.wait_for_timeout(3000)  # extra breathing room before trying again
        else:
            raise TimeoutError(f"No download started for {brand_name} after {max_clicks} clicks")
        download = downloads[0]
    finally:
        page.remove_listener("download", handler)

    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(download.suggested_filename).suffix or ".csv"
    dest = config.DOWNLOAD_DIR / f"{_safe_filename(brand_name)}{suffix}"
    download.save_as(dest)
    logger.info("Saved %s export to %s", brand_name, dest)
    return dest


def screenshot_on_failure(page, label: str):
    try:
        config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = config.SCREENSHOT_DIR / f"{label}_{ts}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.info("Saved failure screenshot to %s", path)
    except Exception:
        logger.error("Could not capture failure screenshot:\n%s", traceback.format_exc())


# ---------------------------------------------------------------------------
# Data handling
# ---------------------------------------------------------------------------
def load_export(path: Path, brand_name: str) -> pd.DataFrame:
    # Force ID-like columns to stay text. Without this, pandas auto-detects
    # UPC as a number and silently drops any leading zero -- confirmed via
    # testing that this actually happens with real TrackStreet exports.
    # extra columns in this dict beyond what's in a given file are ignored
    # by pandas, so this is safe even if a brand's export is missing one.
    id_columns = {
        "UPC": str, "SKU": str, "Website URL ID": str, "Website Item Number": str,
    }

    if path.suffix.lower() == ".csv":
        # utf-8-sig transparently strips a UTF-8 BOM if TrackStreet's export
        # includes one (common from browser-triggered CSV exports), and
        # behaves like plain utf-8 if it doesn't -- safe either way.
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=id_columns)
    elif path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=id_columns)
    else:
        raise ValueError(f"Unrecognized export file type: {path}")

    if df.empty:
        logger.warning("Export for %s has 0 rows -- check for an upstream issue.", brand_name)

    if len(df.columns) <= 1:
        logger.warning(
            "%s export parsed into only %d column(s) -- it may not be comma-delimited "
            "as expected. Check %s directly before trusting this run's output.",
            brand_name, len(df.columns), path,
        )

    df.insert(0, "Brand", brand_name)
    return df


def combine_exports(paths_by_brand: dict) -> pd.DataFrame:
    frames = []
    column_sets = {}
    for brand, path in paths_by_brand.items():
        df = load_export(path, brand)
        frames.append(df)
        column_sets[brand] = set(df.columns) - {"Brand"}

    # If one brand's export has different columns than the others (a filter
    # setting, a permissions difference, etc.), pd.concat below will still
    # "succeed" by filling the gaps with NaN -- log it so that shows up
    # here instead of as an unexplained gap in Tableau weeks from now.
    reference_brand, reference_cols = next(iter(column_sets.items()))
    for brand, cols in column_sets.items():
        if cols != reference_cols:
            logger.warning(
                "Column mismatch: %s vs %s -- missing: %s, extra: %s",
                brand, reference_brand,
                sorted(reference_cols - cols) or "none",
                sorted(cols - reference_cols) or "none",
            )

    combined = pd.concat(frames, ignore_index=True, sort=False)
    logger.info("Combined %d brand export(s) into %d total rows", len(frames), len(combined))
    return combined


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean up known TrackStreet export quirks before writing to Tableau's
    data source. Every transformation here was confirmed against the full
    contents of real downloaded files (all rows, all 4 brands), not a
    sample -- see project notes for how each was verified."""
    df = df.copy()

    # "$1,234.56" (text) -> 1234.56 (float). Confirmed via a full-file
    # regex check: every value in both columns matches this exact format
    # across all 4 brands, so no error-handling fallback is needed here.
    for col in ("MAP", "Offer Price"):
        if col in df.columns:
            df[col] = (
                df[col].str.replace("$", "", regex=False)
                       .str.replace(",", "", regex=False)
                       .astype(float)
            )

    # "09/09/2026 12:59 PM UTC" (text) -> real datetime. Confirmed via a
    # full-file regex check: every value matches this exact format.
    if "Date/Time" in df.columns:
        df["Date/Time"] = pd.to_datetime(df["Date/Time"], format="%m/%d/%Y %I:%M %p UTC")

    # "Yes" / blank -> real True/False. Confirmed via full-file value
    # counts: no third value ever appears in any of these columns.
    for col in ("Buy Box Winner", "Has Amazon Discount", "Out of Stock", "Price In Cart"):
        if col in df.columns:
            df[col] = df[col] == "Yes"

    # Pad back to the standard 12 digits. Confirmed: Zurn has 99 rows
    # missing a leading zero in TrackStreet's own raw export (not
    # something our pipeline introduced) -- this fixes those without
    # needing to know which specific rows they are.
    if "UPC" in df.columns:
        df["UPC"] = df["UPC"].str.zfill(12)

    # Known typo in TrackStreet's own product data, confirmed by eye:
    # "Elkey" should be "Elkay".
    if "Product Title" in df.columns:
        df["Product Title"] = df["Product Title"].str.replace("Elkey", "Elkay", regex=False)

    return df


def write_output_atomically(df: pd.DataFrame):
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = config.OUTPUT_PATH.with_suffix(config.OUTPUT_PATH.suffix + ".tmp")
    df.to_csv(tmp_path, index=False)
    # Path.replace() (os.replace) -- NOT shutil.move(). shutil.move() tries
    # os.rename() first, and on Windows os.rename() raises FileExistsError
    # when the destination already exists (unlike POSIX), so it would fall
    # back to a non-atomic copy+delete on every run after the first -- the
    # exact partial-read risk this write-to-temp-then-swap pattern exists
    # to prevent. os.replace() is atomic on both Windows and POSIX.
    tmp_path.replace(config.OUTPUT_PATH)
    logger.info("Wrote combined file to %s (%d rows)", config.OUTPUT_PATH, len(df))


# ---------------------------------------------------------------------------
# Overlap protection
# ---------------------------------------------------------------------------
def acquire_lock() -> bool:
    """Returns True if we got the lock and should proceed, False if another
    run appears to already be in progress."""
    if config.LOCK_FILE.exists():
        age_minutes = (time.time() - config.LOCK_FILE.stat().st_mtime) / 60
        if age_minutes < config.LOCK_MAX_AGE_MINUTES:
            return False  # another run is very likely still active
        logger.warning(
            "Found a lock file %.0f minutes old -- treating it as left over "
            "from a crashed run rather than an active one. Proceeding.", age_minutes,
        )
    config.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.LOCK_FILE.write_text(str(time.time()))
    # atexit runs this on normal completion, any sys.exit(), an unhandled
    # error, or Ctrl+C -- covering every exit path in run() below without
    # needing a release_lock() call at each one individually. Only a true
    # forced kill (Task Manager "End Task", a power loss) would skip this --
    # that's what the staleness check above is the backup for.
    atexit.register(release_lock)
    return True


def release_lock():
    try:
        config.LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        logger.error("Could not remove lock file:\n%s", traceback.format_exc())


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run():
    setup_logging()
    logger.info("=== TrackStreet sync starting ===")

    if not acquire_lock():
        msg = (
            "Another run appears to already be in progress (a recent lock "
            "file exists) -- skipping this run rather than risk two "
            "instances colliding over the same files."
        )
        logger.warning(msg)
        send_alert("Skipped -- run already in progress", msg)
        sys.exit(3)  # distinct from 0/1/2 -- skipped, not a real failure

    if not config.TRACKSTREET_EMAIL or not config.TRACKSTREET_PASSWORD:
        msg = "TRACKSTREET_EMAIL / TRACKSTREET_PASSWORD missing from .env"
        logger.error(msg)
        send_alert("Config error", msg)
        sys.exit(1)

    paths_by_brand = {}
    failed_brands = []

    with sync_playwright() as p:
        launch_kwargs = {"headless": config.HEADLESS}
        if config.BROWSER_CHANNEL:
            launch_kwargs["channel"] = config.BROWSER_CHANNEL

        # A persistent profile, not a fresh throwaway browser -- so
        # TrackStreet's device-verification email only needs to be handled
        # once every ~30 days by a human, instead of on every scheduled run.
        config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        context = p.chromium.launch_persistent_context(str(config.PROFILE_DIR), **launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(config.NAV_TIMEOUT_MS)

        try:
            with_retries(login, page, what="Login")
            with_retries(navigate_to_reports, page, what="Navigate to reports")
        except Exception as exc:
            logger.error("Login/navigation failed after retries: %s", exc)
            screenshot_on_failure(page, "login_failure")
            context.close()
            send_alert(
                "Login failed -- run aborted",
                f"{exc}\n\nIf the attached screenshot shows a 'verify this "
                f"device' or email-verification page, the 30-day device "
                f"trust likely expired. Fix: someone needs to run "
                f"debug_explore.py once, headed, and complete verification "
                f"manually -- see README.\n\nRecent log:\n{tail_log()}",
            )
            sys.exit(1)

        # NOTE: all 4 brands share one page/session. If a failed export
        # leaves the UI in a bad state (a stuck modal, an error toast),
        # later brands in this loop could fail too. If that turns out to
        # be a real problem in practice, add a recovery step here (e.g.
        # page.goto back to a known-good dashboard URL) between brands.
        for brand_name in config.BRANDS:
            try:
                path = with_retries(export_brand, page, brand_name, what=f"Export ({brand_name})")
                paths_by_brand[brand_name] = path
            except Exception as exc:
                logger.error("Export failed for %s: %s", brand_name, exc)
                screenshot_on_failure(page, f"export_failure_{brand_name}")
                failed_brands.append(brand_name)

        context.close()

    if not paths_by_brand:
        msg = "All brand exports failed -- nothing to combine."
        logger.error(msg)
        send_alert("All exports failed", f"{msg}\n\nRecent log:\n{tail_log()}")
        sys.exit(1)

    try:
        combined = combine_exports(paths_by_brand)
        combined = clean_dataframe(combined)
        write_output_atomically(combined)
    except Exception as exc:
        logger.error("Combine/write step failed: %s\n%s", exc, traceback.format_exc())
        send_alert("Combine/write failed", f"{exc}\n\nRecent log:\n{tail_log()}")
        sys.exit(1)

    if failed_brands:
        msg = (
            f"Sync completed but {len(failed_brands)} brand(s) failed and were "
            f"excluded from the combined file: {', '.join(failed_brands)}."
        )
        logger.warning(msg)
        send_alert("Partial success -- some brands missing", f"{msg}\n\nRecent log:\n{tail_log()}")
        sys.exit(2)  # distinct from 0 (full success) and 1 (hard failure) in Task Scheduler's history
    else:
        logger.info("=== TrackStreet sync completed successfully ===")


if __name__ == "__main__":
    run()
