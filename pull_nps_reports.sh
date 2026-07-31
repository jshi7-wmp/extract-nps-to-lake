#!/usr/bin/env bash
#
# pull_nps_reports.sh
#
# Rename Medallia NPS response exports and upload them to GCS.
#
# The CSV extraction from the Medallia portal is a manual, SSO-authenticated
# browser step (Feedback -> Export -> CSV). This script automates everything
# after the download: it locates the latest export for each brand, renames it
# to the required pattern, and uploads it to the GCS "raw" landing bucket.
#
# Manual extract references (open in a browser, export as CSV):
#   Big W Market  (BWM)  - Last 14 Days
#     https://woolworthsgroup.medallia.com.au/sso/woolworthsgroup/applications/ex_WEB-9/pages/4035
#   EDR Shop      (EDRS) - Last 14 Days
#     https://woolworthsgroup.medallia.com.au/sso/woolworthsgroup/applications/ex_WEB-9/pages/4036
#   Everyday Market (EDM)
#     https://woolworthsgroup.medallia.com.au/sso/woolworthsgroup/applications/ex_WEB-9/pages/1035
#
# File patterns:
#   BWM  - Responses_BWM_last_14_days_DDMMYYYY.csv
#   EDRS - Responses_EDRS_last_14_days_DDMMYYYY.csv
#   EDM  - AU_FOOD_Responses_Export_YYYY-MM-DD_HH_MM_SS.csv
#          (original download name with spaces replaced by underscores)
#
# BWM and EDRS both download as "Responses.csv", so their filenames are
# indistinguishable. When processing a single brand you can point at the exact
# file with -f; otherwise the script picks the newest "Responses*.csv" and
# refuses to reuse the same file for both brands in one run.
#
# Usage:
#   ./pull_nps_reports.sh [-d DOWNLOAD_DIR] [-w WORK_DIR] [-b BRANDS] [-f FILE]
#                         [-D] [-H] [-p PROFILE_DIR] [-n] [-S]
#
#   -d DOWNLOAD_DIR  Directory holding the raw Medallia downloads (default: ~/Downloads)
#   -w WORK_DIR      Directory to place renamed files      (default: ./medallia_upload)
#   -b BRANDS        Comma-separated subset to process     (default: BWM,EDRS,EDM)
#   -f FILE          Explicit source CSV (only valid with a single -b brand)
#   -D               Download from Medallia first (Playwright, SSO login reused)
#   -H               Run the download browser headless (breaks first-time login)
#   -p PROFILE_DIR   Persistent browser profile dir (default: ~/.medallia_playwright_profile)
#   -n               Dry run: show actions without downloading/renaming/uploading
#   -S               Skip CSV header/column validation before upload
#
# Downloading (-D) drives a real Chromium browser via medallia_download.py.
# It first runs a dedicated LOGIN step: a window opens and you complete SSO
# (incl. MFA) once; the session is saved to PROFILE_DIR and reused for every
# brand, so the actual downloads are hands-off. If a later export button can't
# be auto-clicked, the browser stays open for you to click Export -> CSV
# manually and the download is still captured.
#
# Typical BWM/EDRS workflow (same filename, so do one at a time):
#   ./pull_nps_reports.sh -b BWM  -f ~/Downloads/Responses.csv
#   ./pull_nps_reports.sh -b EDRS -f ~/Downloads/Responses.csv
#
# Or fetch + rename + upload everything end to end:
#   ./pull_nps_reports.sh -D
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
GCS_BUCKET="gs://gcp-wow-wmp-ai-data-prod-data-store/data_extract/medallia/raw"

DOWNLOAD_DIR="${HOME}/Downloads"
WORK_DIR="$(pwd)/medallia_upload"
BRANDS="BWM,EDRS,EDM"
SRC_FILE=""
DRY_RUN=0
DOWNLOAD=0
HEADLESS=0
CHECK_HEADERS=1
PROFILE_DIR="${HOME}/.medallia_playwright_profile"
# Prefer python3.11 (where Playwright is installed) unless PY_BIN is overridden.
if [[ -z "${PY_BIN:-}" ]]; then
    if command -v python3.11 >/dev/null 2>&1; then
        PY_BIN="python3.11"
    else
        PY_BIN="python3"
    fi
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADER="${SCRIPT_DIR}/medallia_download.py"
HEADER_CHECKER="${SCRIPT_DIR}/check_headers.py"

# Full Medallia report URLs (reconstructed from the calendar redirect links).
BWM_URL='https://woolworthsgroup.medallia.com.au/sso/woolworthsgroup/applications/ex_WEB-9/pages/4035?roleId=28862&f.benchmark=all&f.timeperiod=508&f.reporting-date=k_bp_timezone_response_time&fi.benchmark=all&fi.pfe_woolworthsgroup_survey_program_alt=681_42&fi.pfe_woolworthsgroup_store_no_unit=1636780&fi.timeperiod=341&fi.reporting-date=k_bp_timezone_response_time'
EDRS_URL='https://woolworthsgroup.medallia.com.au/sso/woolworthsgroup/applications/ex_WEB-9/pages/4036?roleId=649&f.feedback-type=all-feedback&f.question-score=a_overall_score_with_social_media_5_buckets&f.benchmark=all&f.pfe_woolworthsgroup_survey_program_alt=681_43&f.timeperiod=341&f.reporting-date=k_bp_timezone_response_time'
EDM_URL='https://woolworthsgroup.medallia.com.au/sso/woolworthsgroup/applications/ex_WEB-9/pages/1035?roleId=649&f.benchmark=all&f.feedback-type=all-feedback&f.pfe_woolworthsgroup_survey_program_alt=681_34&f.pfe_woolworthsgroup_store_no_unit=1525181&f.question-score=a_overall_score_with_social_media_5_buckets&f.timeperiod=341&f.reporting-date=k_bp_timezone_response_time'

# Per-brand export control: which menu to open and which item text to click.
# BWM/EDRS use the responses-card kebab ("⋮") menu -> CSV.
# EDM uses the top toolbar download icon -> CSV Single Line.
BWM_MENU="kebab";   BWM_LABEL="CSV"
EDRS_MENU="kebab";  EDRS_LABEL="CSV"
EDM_MENU="toolbar"; EDM_LABEL="CSV Single Line"

# Glob patterns used to find the newest raw download for each brand.
# Adjust if Medallia's default export names change.
# BWM and EDRS both export as "Responses.csv" (browser may add " (1)", etc.).
BWM_SRC_GLOB='Responses*.csv'
EDRS_SRC_GLOB='Responses*.csv'
EDM_SRC_GLOB='AU FOOD Responses Export*.csv AU_FOOD_Responses_Export*.csv'

# Tracks source files already consumed in this run (prevents BWM and EDRS from
# both grabbing the same Responses*.csv).
USED_SRCS=""

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date '+%H:%M:%S')" "$*" >&2; exit 1; }

usage() {
    sed -n '2,56p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# Print the newest file matching any of the space-separated glob patterns
# provided as a single argument. Prints nothing if there is no match.
newest_match() {
    local dir="$1" patterns="$2"
    local newest="" f
    for pat in $patterns; do
        while IFS= read -r -d '' f; do
            if [[ -z "$newest" || "$f" -nt "$newest" ]]; then
                newest="$f"
            fi
        done < <(find "$dir" -maxdepth 1 -type f -name "$pat" -print0 2>/dev/null)
    done
    [[ -n "$newest" ]] && printf '%s' "$newest"
}

# Validate that a CSV's header row matches the expected columns for a brand.
# Returns 0 if the columns are as expected (or validation is skipped/unavailable),
# non-zero if expected columns are missing. Extra/new columns are a warning only.
check_headers() {
    local brand="$1" src="$2"

    [[ "$CHECK_HEADERS" -eq 1 ]] || return 0

    if [[ ! -f "$HEADER_CHECKER" ]]; then
        warn "$brand: header checker not found ($HEADER_CHECKER); skipping validation."
        return 0
    fi
    if ! command -v "$PY_BIN" >/dev/null 2>&1; then
        warn "$brand: $PY_BIN not found; skipping header validation."
        return 0
    fi

    "$PY_BIN" "$HEADER_CHECKER" --brand "$brand" --file "$src"
}

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
while getopts ':d:w:b:f:p:DHnSh' opt; do
    case "$opt" in
        d) DOWNLOAD_DIR="$OPTARG" ;;
        w) WORK_DIR="$OPTARG" ;;
        b) BRANDS="$OPTARG" ;;
        f) SRC_FILE="$OPTARG" ;;
        p) PROFILE_DIR="$OPTARG" ;;
        D) DOWNLOAD=1 ;;
        H) HEADLESS=1 ;;
        n) DRY_RUN=1 ;;
        S) CHECK_HEADERS=0 ;;
        h) usage 0 ;;
        :) die "Option -$OPTARG requires an argument." ;;
        \?) die "Unknown option -$OPTARG (use -h for help)." ;;
    esac
done

if [[ -n "$SRC_FILE" ]]; then
    [[ -f "$SRC_FILE" ]] || die "Source file not found: $SRC_FILE"
    [[ "$BRANDS" != *,* ]] || die "-f can only be used with a single -b brand."
    [[ "$DOWNLOAD" -eq 0 ]] || die "-f and -D are mutually exclusive."
fi

if [[ "$DOWNLOAD" -eq 1 ]]; then
    [[ -f "$DOWNLOADER" ]] || die "Downloader not found: $DOWNLOADER"
    command -v "$PY_BIN" >/dev/null 2>&1 \
        || die "$PY_BIN not found. Install Python 3, or set PY_BIN to your interpreter."
fi

[[ -d "$DOWNLOAD_DIR" ]] || die "Download directory not found: $DOWNLOAD_DIR"

if [[ "$DRY_RUN" -eq 0 ]]; then
    command -v gsutil >/dev/null 2>&1 \
        || die "gsutil not found. Install the Google Cloud SDK and run 'gcloud auth login'."
fi

mkdir -p "$WORK_DIR"

TODAY_DDMMYYYY="$(date '+%d%m%Y')"

# ----------------------------------------------------------------------------
# Per-brand processing
# ----------------------------------------------------------------------------
# Given a source file and a target filename, copy into WORK_DIR then upload.
process_file() {
    local src="$1" target_name="$2"
    local target="${WORK_DIR}/${target_name}"

    log "Source : $src"
    log "Rename : $target_name"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "DRY RUN: would copy to $target and upload to $GCS_BUCKET/"
        return 0
    fi

    cp -p -- "$src" "$target"
    log "Upload : $GCS_BUCKET/$target_name"
    gsutil cp -- "$target" "$GCS_BUCKET/$target_name"
    log "Done   : $target_name"
}

# Download one brand's CSV from Medallia into the given staging path via
# Playwright. Echoes the portal's suggested filename (stdout) on success.
run_downloader() {
    local brand="$1" url="$2" menu="$3" label="$4" staging="$5"
    local hl_flag=()
    [[ "$HEADLESS" -eq 1 ]] && hl_flag=(--headless)
    "$PY_BIN" "$DOWNLOADER" \
        --brand "$brand" \
        --url "$url" \
        --menu "$menu" \
        --export-label "$label" \
        --save-as "$staging" \
        --profile-dir "$PROFILE_DIR" \
        ${hl_flag[@]+"${hl_flag[@]}"}
}

# Establish the Medallia SSO session once, up front, before any downloads.
# Opens the browser (unless -H) and waits for an authenticated report to load;
# once the session is saved to PROFILE_DIR the per-brand downloads run hands-off.
medallia_login() {
    local hl_flag=()
    [[ "$HEADLESS" -eq 1 ]] && hl_flag=(--headless)
    log "==== LOGIN ===="
    log "Establishing Medallia SSO session (complete the login in the browser if prompted)..."
    "$PY_BIN" "$DOWNLOADER" \
        --login \
        --brand LOGIN \
        --url "$BWM_URL" \
        --menu kebab \
        --profile-dir "$PROFILE_DIR" \
        ${hl_flag[@]+"${hl_flag[@]}"}
}

process_brand() {
    local brand="$1"
    local src target_name url menu label suggested="" staging

    case "$brand" in
        BWM)  url="$BWM_URL";  menu="$BWM_MENU";  label="$BWM_LABEL"  ;;
        EDRS) url="$EDRS_URL"; menu="$EDRS_MENU"; label="$EDRS_LABEL" ;;
        EDM)  url="$EDM_URL";  menu="$EDM_MENU";  label="$EDM_LABEL"  ;;
        *)    warn "Unknown brand '$brand'; skipping."; return 1 ;;
    esac

    log "==== $brand ===="

    if [[ "$DOWNLOAD" -eq 1 ]]; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
            log "DRY RUN: would download $brand via $menu -> '$label'"
            log "         $url"
            return 0
        fi
        staging="${WORK_DIR}/.dl_${brand}.csv"
        rm -f -- "$staging"
        log "Download: $brand via $menu -> '$label'"
        if ! suggested="$(run_downloader "$brand" "$url" "$menu" "$label" "$staging")"; then
            warn "$brand: download failed; skipping."
            return 1
        fi
        [[ -f "$staging" ]] || { warn "$brand: downloaded file missing; skipping."; return 1; }
        src="$staging"
    else
        # Locate the already-downloaded CSV in DOWNLOAD_DIR (or -f override).
        case "$brand" in
            BWM)  src="${SRC_FILE:-$(newest_match "$DOWNLOAD_DIR" "$BWM_SRC_GLOB")}"  ;;
            EDRS) src="${SRC_FILE:-$(newest_match "$DOWNLOAD_DIR" "$EDRS_SRC_GLOB")}" ;;
            EDM)  src="${SRC_FILE:-$(newest_match "$DOWNLOAD_DIR" "$EDM_SRC_GLOB")}"  ;;
        esac
        [[ -n "$src" ]] || { warn "No $brand download found in $DOWNLOAD_DIR; skipping."; return 1; }

        # Refuse to consume the same source file twice (BWM/EDRS share a name).
        if [[ -z "$SRC_FILE" && "$USED_SRCS" == *"|${src}|"* ]]; then
            warn "$brand: source '$src' already used this run. Download the $brand file separately or pass -f; skipping."
            return 1
        fi
        USED_SRCS="${USED_SRCS}|${src}|"
    fi

    # Validate the export's header row before renaming/uploading. Catches
    # Medallia layout changes (renamed/added/removed survey questions).
    if ! check_headers "$brand" "$src"; then
        warn "$brand: header validation failed for '$src'; skipping upload (use -S to override)."
        return 1
    fi

    # Determine the target filename per the required naming patterns.
    case "$brand" in
        BWM)  target_name="Responses_BWM_last_14_days_${TODAY_DDMMYYYY}.csv"  ;;
        EDRS) target_name="Responses_EDRS_last_14_days_${TODAY_DDMMYYYY}.csv" ;;
        EDM)
            # EDM keeps the export's own name, spaces replaced with underscores.
            if [[ -n "$suggested" ]]; then
                target_name="$(printf '%s' "$suggested" | tr ' ' '_')"
            else
                target_name="$(basename "$src" | tr ' ' '_')"
            fi
            ;;
    esac

    process_file "$src" "$target_name"
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
log "Download dir : $DOWNLOAD_DIR"
log "Work dir     : $WORK_DIR"
log "GCS bucket   : $GCS_BUCKET"
log "Brands       : $BRANDS"
[[ "$DRY_RUN" -eq 1 ]] && log "Mode         : DRY RUN"

# Log into Medallia once up front so the per-brand downloads are hands-off.
if [[ "$DOWNLOAD" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    medallia_login \
        || die "Could not establish Medallia SSO session; re-run and complete the login in the browser window."
fi

processed=0
failed=0
IFS=',' read -r -a brand_list <<< "$BRANDS"
for brand in "${brand_list[@]}"; do
    brand="$(echo "$brand" | tr '[:lower:]' '[:upper:]' | xargs)"
    [[ -n "$brand" ]] || continue
    if process_brand "$brand"; then
        processed=$((processed + 1))
    else
        failed=$((failed + 1))
    fi
done

log "Summary: $processed processed, $failed skipped/failed."
[[ "$failed" -eq 0 ]] || exit 1
