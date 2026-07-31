#!/usr/bin/env python3
"""Download a single Medallia report CSV via Playwright.

The Medallia portal sits behind Woolworths SSO, so this uses a *persistent*
browser profile: the first time you run it a real Chromium window opens and you
log in (including MFA) once. The session is saved to the profile directory and
reused on later runs, so subsequent downloads are hands-off.

For each report the script navigates to the report URL, then tries to trigger
the CSV export automatically (kebab "⋮" menu for BWM/EDRS, the toolbar download
icon for EDM). If the export control can't be found/clicked, it falls back to
*assisted* mode: it waits for you to click Export -> CSV yourself and captures
whatever CSV download the page produces.

It prints the download's suggested filename to stdout (used by the caller to
name the EDM file). All diagnostics go to stderr.

Exit codes: 0 success, 2 usage/setup error, 3 download failed/timed out.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def open_kebab_menu(page) -> None:
    """Open the responses-widget kebab (BWM/EDRS).

    Locate the "N responses" header, walk up to the widget that owns a popup
    IconButton, and click that kebab. Confirmed against the ex_WEB report pages.
    """
    count = page.get_by_text(re.compile(r"\d[\d,]*\s+responses", re.I)).first
    count.scroll_into_view_if_needed(timeout=8000)
    widget = count.locator(
        "xpath=ancestor::*[.//button[@data-testid='IconButton' "
        "and @aria-haspopup='true']][1]"
    )
    widget.locator(
        "button[data-testid='IconButton'][aria-haspopup='true']"
    ).last.click(timeout=8000)


def open_toolbar_menu(page) -> None:
    """Open the top toolbar export/download menu (EDM)."""
    for sel in ("#openExportMenu",
                "button[aria-label*='download' i]",
                "button[aria-label*='export' i]"):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                return
        except Exception:
            continue
    raise RuntimeError("toolbar export/download button not found")


def click_export_label(page, label: str) -> None:
    """Click the export menu item with the given text (e.g. 'CSV')."""
    # The menu items render as button[role=menuitem]; fall back to plain text.
    try:
        page.get_by_role("menuitem", name=label, exact=True).first.click(timeout=5000)
    except Exception:
        page.get_by_text(label, exact=True).first.click(timeout=5000)


def try_auto_export(page, menu: str, label: str, timeout_ms: int):
    """Attempt a fully automated export. Returns a Download or None."""
    try:
        with page.expect_download(timeout=timeout_ms) as dl_info:
            if menu == "toolbar":
                open_toolbar_menu(page)
            else:
                open_kebab_menu(page)
            page.wait_for_timeout(600)
            click_export_label(page, label)
        return dl_info.value
    except Exception as exc:  # noqa: BLE001 - fall back to manual
        eprint(f"[medallia] auto-export failed ({exc}); switching to manual mode.")
        return None


def report_ready_locator(page, menu: str):
    """A locator for the report's export control, used to detect that the
    authenticated report has finished rendering."""
    if menu == "toolbar":
        return page.locator(
            "#openExportMenu, button[aria-label*='download' i], "
            "button[aria-label*='export' i]"
        ).first
    return page.get_by_text(re.compile(r"\d[\d,]*\s+responses", re.I)).first


def wait_for_report_ready(page, menu: str, timeout_ms: int) -> None:
    """Block until the report's export control is visible.

    This spans the SSO login: while the browser is on the IdP login page the
    control is absent, so the poll keeps waiting until the authenticated report
    renders (or the timeout elapses). Ensuring the controls exist *before* we
    auto-click makes the export reliable after a fresh login.
    """
    report_ready_locator(page, menu).wait_for(state="visible", timeout=timeout_ms)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a Medallia report CSV.")
    parser.add_argument("--url", required=True, help="Full report URL.")
    parser.add_argument("--save-as", help="Path to write the CSV (not needed with --login).")
    parser.add_argument("--brand", default="", help="Brand tag (for logging).")
    parser.add_argument("--menu", choices=["kebab", "toolbar"], default="kebab",
                        help="Which export control to use.")
    parser.add_argument("--export-label", default="CSV",
                        help="Text of the export menu item to click.")
    parser.add_argument("--profile-dir", required=True,
                        help="Persistent browser profile directory.")
    parser.add_argument("--headless", action="store_true",
                        help="Run without a visible window (breaks first-time login).")
    parser.add_argument("--auto-timeout", type=int, default=45,
                        help="Seconds to wait for automated export.")
    parser.add_argument("--manual-timeout", type=int, default=300,
                        help="Seconds to wait for a manual export / login.")
    parser.add_argument("--ready-timeout", type=int, default=90,
                        help="Seconds to wait for the report (through SSO) to load.")
    parser.add_argument("--login", action="store_true",
                        help="Only establish the SSO session, then exit (no download).")
    args = parser.parse_args()

    if not args.login and not args.save_as:
        parser.error("--save-as is required unless --login is given.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        eprint("[medallia] Playwright is not installed. Run:")
        eprint("    python3 -m pip install playwright")
        eprint("    python3 -m playwright install chromium")
        return 2

    save_as = None
    if args.save_as:
        save_as = Path(args.save_as).expanduser()
        save_as.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile_dir).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)

    tag = f"[medallia:{args.brand}]" if args.brand else "[medallia]"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            accept_downloads=True,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            eprint(f"{tag} navigating to report...")
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)

            if args.login:
                eprint(
                    f"{tag} Complete the Woolworths SSO login (incl. MFA) in the "
                    f"browser window if prompted."
                )
                eprint(
                    f"{tag} Waiting up to {args.manual_timeout}s for an "
                    f"authenticated report to load..."
                )
                try:
                    wait_for_report_ready(page, args.menu, args.manual_timeout * 1000)
                except Exception:
                    eprint(f"{tag} ERROR: report did not load; SSO not confirmed.")
                    return 3
                eprint(f"{tag} SSO session established; downloads can now run hands-off.")
                return 0

            # Wait for the report to render (spans any residual SSO redirect) so
            # the export controls exist before we try to auto-click them.
            ready = True
            try:
                wait_for_report_ready(page, args.menu, args.ready_timeout * 1000)
            except Exception:
                ready = False
                eprint(f"{tag} report controls not visible; will rely on manual export.")

            download = None
            if ready:
                download = try_auto_export(
                    page, args.menu, args.export_label, args.auto_timeout * 1000
                )

            if download is None:
                eprint(
                    f"{tag} Please complete SSO login if prompted, then click "
                    f"Export -> '{args.export_label}' in the browser window."
                )
                eprint(f"{tag} Waiting up to {args.manual_timeout}s for the download...")
                try:
                    download = page.wait_for_event(
                        "download", timeout=args.manual_timeout * 1000
                    )
                except Exception:
                    eprint(f"{tag} ERROR: no download detected before timeout.")
                    return 3

            suggested = download.suggested_filename
            download.save_as(str(save_as))
            eprint(f"{tag} saved -> {save_as}")
            # stdout: the portal's suggested filename (used for EDM naming).
            print(suggested)
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    sys.exit(main())
