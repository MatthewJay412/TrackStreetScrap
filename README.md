# TrackStreet → Tableau Sync

This grabs fresh pricing/violation data straight from TrackStreet's website (way more current than the Snowflake feed), cleans it up, and drops one tidy file for Tableau to read. No more logging in and manually exporting 4 separate brands by hand.

**Heads up:** this whole thing was built and tested on a Mac, but it's meant to *run* on a Windows machine long-term. Everything works the same either way — the differences are just a few commands (covered below) and one extra setup step you'll do once on the Windows machine itself.

## The short version of how it works

1. Opens a real (invisible, once you're comfortable with it) browser and logs into TrackStreet
2. Navigates to the right report and sets the time filter to "Last Crawl"
3. Switches through all 4 brands one at a time, exporting each as a CSV
4. Combines all 4 into one file, cleans up some formatting quirks (more on that below), and saves it
5. Logs everything it does, and emails you if something goes wrong

## Files in here

| File | What it's for |
|---|---|
| `config.py` | All the settings — credentials, folder paths, brand names, timeouts. Reads from `.env`. |
| `trackstreet_sync.py` | **The real script.** This is what you actually run, and what gets scheduled. |
| `debug_explore.py` | A helper for poking around TrackStreet's site and finding selectors, if the UI ever changes and something breaks. |
| `check_data_patterns.py` | A one-off tool we used to sanity-check the real data (prices, dates, UPCs) before writing the cleaning logic. Not part of the regular run, but handy if you ever want to re-check the data. |
| `debug_export_timing.py` | Another one-off — this is what we used to figure out why exports needed multiple clicks. Probably won't need it again, but it's here. |
| `.env.example` | Template for your real `.env` file. |
| `requirements.txt` | Python packages this needs. |
| `run_sync.bat` | What Task Scheduler actually points at (not the `.py` file directly). |

## Setting it up (first time on a new machine)

**On Mac** (for testing):
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium --force
```

**On Windows** (the real deal):
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Then either way:
```
copy .env.example .env      (Windows)
cp .env.example .env        (Mac)
```
Fill in your real TrackStreet email/password, and set `BASE_DIR`/`OUTPUT_DIR` to wherever you want everything to live — Windows paths look like `C:\TrackStreetSync`, Mac paths look like `/Users/you/Desktop/TS/data`.

**One important thing about moving to Windows:** the "remember me" browser profile that avoids TrackStreet's email verification every run doesn't transfer between machines. The first time you run this on Windows, you'll probably need to verify by email again — that's normal, not a bug. Run `debug_explore.py` once there (visible browser, not scheduled) to get through it, the same way we did on the Mac originally.

## Just want to run it?

```
python3 trackstreet_sync.py     (Mac)
python trackstreet_sync.py      (Windows)
```

Set `HEADLESS=false` in `.env` first if you want to actually watch it happen — good idea the first time on any new machine.

## What it actually protects against

We built a bunch of safety nets into this over time, mostly because we hit each problem for real during testing:

- **Retries everywhere.** Login, navigation, and each brand's export all get a few attempts before giving up.
- **Exports sometimes need more than one click.** TrackStreet's own export button is a little quirky — sometimes the first click just doesn't do anything, no idea why. The script clicks again automatically until a real download actually starts, instead of assuming one click is enough.
- **Screenshots on failure.** If anything does fail, a screenshot gets saved automatically showing exactly what the page looked like at that moment — way easier than guessing what went wrong from a log file alone.
- **Never writes a half-finished file.** The combined file gets written to a temp file first, then swapped in — Tableau will never catch it mid-write.
- **Won't run twice at once.** If Task Scheduler ever kicks off a new run while an old one's still going, the second one politely backs off instead of the two colliding.
- **Email alerts.** Once `SMTP_HOST` is filled in in `.env`, anything that goes wrong sends you an email instead of failing silently where nobody notices.

## The data cleaning step

TrackStreet's raw export has a few quirks that get fixed automatically before the file reaches Tableau (all confirmed against real data, not guessed):

- Prices come through as text like `"$1,234.56"` → converted to real numbers
- Dates come through as text → converted to real dates
- Yes/blank columns (like "Out of Stock") → converted to real True/False
- A small number of Zurn's UPCs are missing their leading zero → padded back to the standard 12 digits
- A typo in TrackStreet's own data ("Elkey" instead of "Elkay") → fixed

## The 30-day email verification thing

TrackStreet occasionally asks you to verify your device by email, and remembers that for a while (usually ~30 days, but not always). We asked TrackStreet to turn this off or extend it for this account — they said no. So here's where things stand:

- The script has a "remembered" browser profile that avoids this most of the time
- When the "Verify your identity" page does come up, the script now handles it itself: it logs into the dedicated Gmail inbox over IMAP, grabs the code from the newest "Your TrackStreet Verification Code" email (only ones that arrived *after* this login, so it never uses a stale code), types it in, ticks "Remember this device", and clicks Log In
- It gives up after 3 tries (`VERIFY_MAX_ATTEMPTS`) so it can't lock the account, and it won't retry the whole login on top of that. If no email shows up within 2 minutes, it clicks Resend Code; that counts as one of the 3 tries
- **Setup (one time):** put the Gmail address and a Google **App Password** in `.env` as `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`. The normal Gmail password won't work over IMAP. To make an App Password, turn on 2-Step Verification for that Gmail account, then go to https://myaccount.google.com/apppasswords
- If it still can't get through (wrong app password, IMAP blocked by the network, codes rejected), it fails that run and emails you; the log says which. The manual fallback still works: run `debug_explore.py` once, by hand, with a visible browser, and click through the verification yourself

## Reading the results of a run

- **Exit code** (shows up in Task Scheduler as "Last Run Result"): `0` = everything worked, `1` = something failed hard (bad login, everything failed), `2` = partial success (file was written, but one or more brands got skipped), `3` = skipped because another run was already in progress.
- **`logs/trackstreet_sync.log`** — the detailed log of a normal run.
- **`logs/failure_screenshots/`** — a picture of exactly what went wrong, if something did.
- **`logs/bat_output.log`** — only has content if something crashed *before* the script's own logging even started (like a broken Python install). Should normally be empty.

## Troubleshooting

- **Antivirus flags it**: this kind of browser automation can look like credential-stuffing tools to antivirus software, even though it's completely legitimate. Get it allowlisted with IT *before* it becomes a problem, not after.
- **Proxy blocks the Chromium download**: set `BROWSER_CHANNEL=msedge` in `.env` to drive the Edge that's already on the machine instead of downloading a new browser.
- **Selectors break after a TrackStreet update**: run `debug_explore.py`, use the Inspector's "Pick locator" tool, find what changed, update `config.py`/`trackstreet_sync.py` to match.
- **Task Scheduler won't let you create a task**: that's usually a Group Policy thing your IT team controls, not something wrong with the script.

## What's actually still left to do

1. Move this whole folder to the Windows machine
2. Redo the Python setup there (see above) and get through the email verification once, on that machine
3. Set up Task Scheduler pointed at `run_sync.bat`
4. Hand the final CSV path off to whoever has Tableau Creator access, so they can wire it up as a data source
5. If it's Tableau Cloud (not Server), that also needs Tableau Bridge set up on an always-on machine — separate conversation with whoever manages that


