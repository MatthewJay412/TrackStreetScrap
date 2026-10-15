"""
Configuration for the TrackStreet -> Tableau sync.

Loads secrets and paths from .env, and defines per-brand settings.

All selectors are confirmed (see trackstreet_sync.py) -- login, brand
switching, navigation, and the Last Crawl time-window filter. One
unconfirmed detail remains: whether selecting "Last Crawl" needs a
separate "Apply" click, flagged inline in navigate_to_reports().
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back to `default` if the
    variable is unset, blank, or not a valid integer -- so a stray blank
    line in .env (e.g. "MAX_RETRIES=" with nothing after it) can't crash
    the script at import time, before logging/alerting even starts."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- Credentials & login ---------------------------------------------------
TRACKSTREET_URL = os.getenv("TRACKSTREET_URL", "https://app.trackstreet.com/login")
TRACKSTREET_EMAIL = os.getenv("TRACKSTREET_EMAIL")
TRACKSTREET_PASSWORD = os.getenv("TRACKSTREET_PASSWORD")

# --- Two-factor verification (read from Gmail over IMAP) -------------------
# TrackStreet sometimes stops after the password and asks for a code it
# emails to the account. The script reads that code from the dedicated
# Gmail inbox and types it in. Leave these blank to disable -- the run then
# fails with an alert, the same as before this existed.
#
# GMAIL_APP_PASSWORD must be a Google *App Password* (16 letters, created at
# https://myaccount.google.com/apppasswords -- needs 2-Step Verification on
# that Gmail account), NOT the normal Gmail password, which IMAP rejects.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
# Google shows app passwords in groups of four ("abcd efgh ..."); strip the
# spaces so a straight copy-paste into .env works.
GMAIL_APP_PASSWORD = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "") or None
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
# Codes submitted (or resend requests) before giving up. Kept low on purpose
# -- too many wrong codes can lock the TrackStreet account.
VERIFY_MAX_ATTEMPTS = _int_env("VERIFY_MAX_ATTEMPTS", 3)
# How long to watch the inbox for the email after each request. TrackStreet's
# codes expire after 5 minutes, so there's no point waiting longer than that.
VERIFY_EMAIL_WAIT_SEC = _int_env("VERIFY_EMAIL_WAIT_SEC", 120)

# --- Working directories ----------------------------------------------------
BASE_DIR = Path(os.getenv("BASE_DIR", r"C:\TrackStreetSync"))
DOWNLOAD_DIR = BASE_DIR / "downloads"   # raw per-brand exports land here temporarily
LOG_DIR = BASE_DIR / "logs"
SCREENSHOT_DIR = LOG_DIR / "failure_screenshots"

# Persistent browser profile (cookies, local storage, etc. survive between
# separate runs of the script -- like a real Chrome profile folder). This is
# what lets TrackStreet's "verify this new device" email only need to be
# handled once every ~30 days by a human, instead of on every single run.
# See README "One-time device verification" before scheduling this.
#
# Keep this OUT of OneDrive (or any synced folder). A downloading Chromium
# writes constantly to its profile, and OneDrive's sync driver locking or
# relocating those files underneath it crashes the browser mid-download --
# exit code 0xC0000005 / 0x80000003, surfacing as "Target page, context or
# browser has been closed". Defaults to LOCALAPPDATA, which is never synced.
# BASE_DIR can stay in OneDrive; only the live profile must be local.
PROFILE_DIR = Path(
    os.getenv("PROFILE_DIR")
    or Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "TrackStreetSync" / "browser_profile"
)

# --- Output ------------------------------------------------------------
# Fixed path Tableau is set up to auto-refresh from. Keep this stable --
# changing it means re-pointing the Tableau data source.
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", r"C:\TrackStreetSync\output"))
OUTPUT_FILENAME = os.getenv("OUTPUT_FILENAME", "trackstreet_combined.csv")
OUTPUT_PATH = OUTPUT_DIR / OUTPUT_FILENAME

# --- Browser -----------------------------------------------------------
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"
# If Playwright's own Chromium download can't reach the internet through
# your proxy, set BROWSER_CHANNEL=msedge in .env to drive the Edge that's
# already installed on the machine instead -- no browser download needed.
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "").strip() or None

# --- Retry / timeouts --------------------------------------------------
# --- Overlap protection ---------------------------------------------------
# If Task Scheduler ever kicks off a new run while a previous one is still
# going (a slow TrackStreet day, a stuck process), this stops the two from
# fighting over the same downloads/output files.
LOCK_FILE = BASE_DIR / "trackstreet_sync.lock"
# No real run has taken more than a few minutes in testing. A lock file
# older than this is treated as left over from a crashed run, not an
# actually-still-running one, and gets ignored.
LOCK_MAX_AGE_MINUTES = _int_env("LOCK_MAX_AGE_MINUTES", 60)

# The browser crashes intermittently mid-download (see export_brand). Each
# crash costs one relaunch + re-login, roughly 15s, and the brand is retried
# on the fresh browser. 5 is enough headroom for 4 brands on a bad day
# without letting a genuinely broken run spin forever.
MAX_BROWSER_RELAUNCHES = _int_env("MAX_BROWSER_RELAUNCHES", 5)

NAV_TIMEOUT_MS = _int_env("NAV_TIMEOUT_MS", 30000)
MAX_RETRIES = _int_env("MAX_RETRIES", 2)
RETRY_DELAY_SEC = _int_env("RETRY_DELAY_SEC", 10)
# Separate timeout just for waiting on the actual CSV file after clicking
# Export. NOTE: raising this to 120000 during testing did NOT fix the
# first-attempt failure (it still timed out at the full duration every
# time), so the cause isn't "needs more time" -- reverted to fail fast
# instead of wasting minutes per run. See debug_export_timing.py.
EXPORT_DOWNLOAD_TIMEOUT_MS = _int_env("EXPORT_DOWNLOAD_TIMEOUT_MS", 30000)
# NOTE: there is deliberately no "wait for the page to settle after a brand
# switch" setting here. Adding one makes the first export click succeed and
# then crashes Edge mid-download -- see the long comment in export_brand().
# The first click failing is expected behaviour, not a bug to tune out.

# How long to leave the download completely alone after the download event
# fires, before reading it with .path() / .save_as().
#
# DO NOT SET THIS TO 0 -- it is load-bearing, not a lazy sleep. Touching the
# Download object while Edge is still writing the file crashes the browser
# outright: process exit code 3221225477 (0xC0000005, access violation),
# which surfaces in the log as the misleading "Target page, context or
# browser has been closed" on save_as. Reproduced deliberately. 5s has run
# clean across all 4 brands; the largest export is ~6.6MB (Elkay Inc.).
# Raise it if exports grow a lot -- the cost is 5s per brand, the downside
# of too low is a hard browser crash that loses the whole run.
DOWNLOAD_SETTLE_SEC = _int_env("DOWNLOAD_SETTLE_SEC", 5)

# --- Email alerts --------------------------------------------------------
# Leave SMTP_HOST blank in .env to disable email alerts (failures will
# still be logged to logs/trackstreet_sync.log either way).
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = _int_env("SMTP_PORT", 587)  # 587 = STARTTLS, 465 = implicit SSL (handled automatically)
SMTP_USER = os.getenv("SMTP_USER")          # leave blank for an unauthenticated internal relay
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() != "false"
ALERT_FROM = os.getenv("ALERT_FROM", "trackstreet-sync@yourcompany.com")
ALERT_TO = [addr.strip() for addr in os.getenv("ALERT_TO", "").split(",") if addr.strip()]

# --- Brands --------------------------------------------------------------
# Real TrackStreet brand names. The brand-switcher dropdown's option text
# matches these exactly, so no per-brand selector is needed -- the export
# logic in trackstreet_sync.py just looks for an option matching each name.
BRANDS = [
    "Elkay Inc.",
    "Elkay Reserve Selection",
    "World Dryer",
    "Zurn",
]
