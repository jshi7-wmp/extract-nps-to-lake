# Medallia NPS Reports → GCS

Automates the Medallia NPS response workflow: download the report CSVs from the
Medallia portal, rename them to the agreed patterns, and upload them to the GCS
`raw` landing bucket.

Files:

- `pull_nps_reports.sh` — orchestrates download → rename → upload.
- `medallia_download.py` — Playwright browser automation for the SSO-protected export.

## What it does

| Brand | Report | Export | Output filename |
|-------|--------|--------|-----------------|
| BWM   | Big W Market — Last 14 Days | kebab `⋮` → CSV | `Responses_BWM_last_14_days_DDMMYYYY.csv` |
| EDRS  | EDR Shop — Last 14 Days | kebab `⋮` → CSV | `Responses_EDRS_last_14_days_DDMMYYYY.csv` |
| EDM   | Everyday Market | toolbar download → CSV Single Line | `AU_FOOD_Responses_Export_YYYY-MM-DD_HH_MM_SS.csv` |

Uploads to: `gs://gcp-wow-wmp-ai-data-prod-data-store/data_extract/medallia/raw`

## Prerequisites

- **Google Cloud SDK** (for `gsutil` uploads):

  ```sh
  brew install --cask google-cloud-sdk
  gcloud auth login
  ```

- **Python 3 + Playwright** (only needed for the `-D` download step):

  ```sh
  python3.11 -m pip install playwright
  python3.11 -m playwright install chromium
  ```

  The script auto-detects `python3.11` (where Playwright is installed). To use a
  different interpreter, set `PY_BIN`, e.g. `PY_BIN=python3.12 ./pull_nps_reports.sh -D`.

## End-to-end usage

Fetch, rename, and upload all three brands. On the **first** run a Chromium
window opens — complete the Woolworths SSO login (incl. MFA) once; the session
is saved and reused afterwards.

```sh
./pull_nps_reports.sh -D
```

Preview without downloading or uploading (no SDK/Playwright required):

```sh
./pull_nps_reports.sh -n -D
```

## Manual-download workflow

If you export the CSVs yourself in the browser, drop them in `~/Downloads` and
run the rename + upload step. BWM and EDRS both download as `Responses.csv`, so
process them one at a time and point at the exact file with `-f`:

```sh
./pull_nps_reports.sh -b BWM  -f ~/Downloads/Responses.csv
./pull_nps_reports.sh -b EDRS -f ~/Downloads/Responses.csv
./pull_nps_reports.sh -b EDM
```

## Options

| Flag | Description | Default |
|------|-------------|---------|
| `-d DOWNLOAD_DIR` | Directory holding the raw Medallia downloads | `~/Downloads` |
| `-w WORK_DIR` | Directory for renamed files | `./medallia_upload` |
| `-b BRANDS` | Comma-separated subset to process | `BWM,EDRS,EDM` |
| `-f FILE` | Explicit source CSV (single `-b` brand only) | — |
| `-D` | Download from Medallia first (Playwright) | off |
| `-H` | Run the download browser headless (breaks first-time login) | off |
| `-p PROFILE_DIR` | Persistent browser profile directory | `~/.medallia_playwright_profile` |
| `-n` | Dry run (no download / rename / upload) | off |

Run `./pull_nps_reports.sh -h` for the built-in help.

## Notes

- Auto-click is implemented for all three brands: BWM/EDRS use the responses
  widget's kebab (`⋮`) → CSV; EDM uses the top toolbar export → CSV Single Line.
  If Medallia changes the UI and a button can't be found, the browser stays open
  and the script waits for you to click Export → CSV manually, then captures the
  download.
- `-f` and `-D` are mutually exclusive; `-f` cannot be combined with multiple
  `-b` brands.
