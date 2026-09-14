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
PROFILE_DIR = BASE_DIR / "browser_profile"

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

NAV_TIMEOUT_MS = _int_env("NAV_TIMEOUT_MS", 30000)
MAX_RETRIES = _int_env("MAX_RETRIES", 2)
RETRY_DELAY_SEC = _int_env("RETRY_DELAY_SEC", 10)
# Separate timeout just for waiting on the actual CSV file after clicking
# Export. NOTE: raising this to 120000 during testing did NOT fix the
# first-attempt failure (it still timed out at the full duration every
# time), so the cause isn't "needs more time" -- reverted to fail fast
# instead of wasting minutes per run. See debug_export_timing.py.
EXPORT_DOWNLOAD_TIMEOUT_MS = _int_env("EXPORT_DOWNLOAD_TIMEOUT_MS", 30000)

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
