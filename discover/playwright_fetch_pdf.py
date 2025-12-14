#!/usr/bin/env python3
"""
playwright_fetch_pdf.py (patched)

Usage:
  python discover/playwright_fetch_pdf.py "<company_name_or_folder>" "<page_url>"

Notes:
- Improved robustness: safe filenames, guaranteed .pdf extension,
  better resource cleanup, defensive network/response handling.
- Requires playwright installed and browsers installed:
    pip install playwright beautifulsoup4 requests
    python -m playwright install
"""

import sys
import os
import re
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse, urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

PDF_TEXT_PAT = re.compile(r"(10[- ]?k|10[- ]?q|annual|quarter|q[1-4]|fy|report|download)", re.I)
COOKIE_BUTTON_PATTERNS = ["accept", "agree", "allow", "consent", "ok"]

# storage root: project_root/storage
STORAGE_ROOT = Path(__file__).resolve().parents[1] / "storage"
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def safe_name(s: str) -> str:
    if not s:
        return ""
    # keep only filename portion and replace unsafe chars
    base = os.path.basename(s)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return safe.strip("_")


def try_click_cookie_buttons(page):
    # try a few strategies to click cookie consent buttons
    selectors = ["button", "a", "input[type=button]", "input[type=submit]"]
    for selector in selectors:
        try:
            els = page.query_selector_all(selector)
        except Exception:
            continue
        for el in els:
            try:
                txt = (el.inner_text() or "").strip().lower()
                val = (el.get_attribute("value") or "").strip().lower()
                if any(p in txt for p in COOKIE_BUTTON_PATTERNS) or any(p in val for p in COOKIE_BUTTON_PATTERNS):
                    try:
                        print("[playwright] clicking cookie button:", (txt or val)[:50])
                        el.click(timeout=3000)
                        time.sleep(0.4)
                        return True
                    except Exception:
                        # ignore click failures on a single element
                        continue
            except Exception:
                continue
    return False


def save_bytes_to_file(b: bytes, company_name: str, suggested_name: str) -> str:
    """
    Save bytes to storage/<company_name>/<suggested_name>.pdf
    Ensures unique filenames and .pdf extension. Returns the saved path as a string.
    """
    folder = STORAGE_ROOT / safe_name(company_name or "unknown_company")
    folder.mkdir(parents=True, exist_ok=True)

    # derive a safe base filename
    suggested = safe_name(suggested_name or "report.pdf")
    # ensure extension is .pdf
    base, ext = os.path.splitext(suggested)
    if not ext:
        ext = ".pdf"
    # normalize extension to .pdf
    ext = ".pdf"
    candidate = folder / f"{base}{ext}"
    # if it exists, append incremental suffix
    i = 1
    while candidate.exists():
        candidate = folder / f"{base}_{i}{ext}"
        i += 1

    # write file
    with open(candidate, "wb") as f:
        f.write(b)
    return str(candidate)


def is_pdf_response(response) -> bool:
    try:
        # response.headers may return a dict-like object
        ct = (response.headers.get("content-type", "") or "")
        url = (response.url or "")
        if "pdf" in ct.lower() or url.lower().endswith(".pdf"):
            return True
    except Exception:
        pass
    return False


def fetch_pdf_via_playwright(company_name: str, page_url: str, headless=True, timeout=30000):
    """
    Return list of dicts: {"source":"anchor"|"network", "pdf_url":..., "saved": "<path>"}
    """
    out = []
    p = None
    browser = None
    context = None
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        pdf_responses = []

        # network response handler
        def on_response(resp):
            try:
                if is_pdf_response(resp):
                    # store object; we'll call resp.body() later in the same sync context
                    print("[playwright] captured pdf response:", resp.url)
                    pdf_responses.append(resp)
            except Exception:
                pass

        page.on("response", on_response)

        print("[playwright] navigating to", page_url)
        try:
            page.goto(page_url, timeout=timeout, wait_until="networkidle")
        except PWTimeoutError:
            # try a more forgiving load
            try:
                page.goto(page_url, timeout=timeout, wait_until="domcontentloaded")
            except Exception as e:
                print("[playwright] navigation failed (both networkidle and domcontentloaded):", e)

        # attempt cookie consent clicks (best-effort)
        try:
            clicked = try_click_cookie_buttons(page)
            if clicked:
                print("[playwright] attempted cookie consent click")
        except Exception:
            pass

        # small wait so that dynamic links can appear
        time.sleep(0.8)

        # 1) collect rendered <a href> links to PDFs
        pdf_links = []
        try:
            anchors = page.query_selector_all("a[href]")
            for a in anchors:
                try:
                    href = a.get_attribute("href")
                    if not href:
                        continue
                    if ".pdf" in href.lower():
                        full = urljoin(page_url, href)
                        text = a.inner_text() or ""
                        pdf_links.append((full, text))
                except Exception:
                    continue
        except Exception:
            # if query_selector_all fails, continue to network-capture approach
            pass

        # If found static PDF links in rendered DOM, download them via Playwright's request (preserves cookies)
        if pdf_links:
            print(f"[playwright] found {len(pdf_links)} pdf anchor(s) in rendered DOM.")
            for url, text in pdf_links:
                try:
                    # use page.request to fetch with current context
                    resp = page.request.get(url, timeout=timeout)
                    if resp and getattr(resp, "status", None) == 200:
                        try:
                            body = resp.body()
                        except Exception as e:
                            print("[playwright] failed to read body for anchor:", url, e)
                            continue
                        filename = os.path.basename(urlparse(url).path) or "report.pdf"
                        saved = save_bytes_to_file(body, company_name, filename)
                        print("[playwright] saved:", saved, "| from anchor:", (text or "")[:80])
                        out.append({"source": "anchor", "pdf_url": url, "saved": saved})
                    else:
                        print(f"[playwright] anchor GET returned status {(resp.status if resp else 'none')} for {url}")
                except Exception as e:
                    print("[playwright] failed to download anchor pdf:", e)
        else:
            # No anchors discovered -> try clicking likely buttons/links and rely on network responses
            print("[playwright] no pdf anchors found in DOM; searching for likely download buttons to click.")

            candidates = []
            try:
                elements = page.query_selector_all("a,button,input[type=button],input[type=submit]")
                for el in elements:
                    try:
                        txt = (el.inner_text() or "").strip()
                        val = (el.get_attribute("value") or "").strip()
                        combined = (txt + " " + val).lower()
                        if PDF_TEXT_PAT.search(combined):
                            candidates.append((combined.strip(), el))
                    except Exception:
                        continue
            except Exception:
                # ignore issues enumerating elements
                candidates = []

            if not candidates:
                print("[playwright] no obvious download buttons found. Waiting briefly for any network-captured PDFs.")
                time.sleep(2.0)
            else:
                print(f"[playwright] attempting to click {len(candidates)} candidate elements that look like download buttons.")
                for idx, (txt, el) in enumerate(candidates):
                    try:
                        print(f"[playwright] clicking candidate #{idx+1}: '{txt[:80]}'")
                        el.click(timeout=5000)
                        # small wait for possible network responses to arrive
                        time.sleep(1.0)
                    except Exception as e:
                        print("[playwright] click failed:", e)
                        continue

            # After clicks/waits, process captured pdf_responses
            if pdf_responses:
                print(f"[playwright] captured {len(pdf_responses)} pdf network response(s). Downloading...")
                for resp in pdf_responses:
                    try:
                        # resp may be a Playwright response object — read body defensively
                        try:
                            body = resp.body()
                        except Exception as e:
                            print("[playwright] unable to read response body for:", resp.url, e)
                            continue
                        parsed = urlparse(resp.url)
                        fname = os.path.basename(parsed.path) or "report.pdf"
                        saved = save_bytes_to_file(body, company_name, fname)
                        print("[playwright] saved network-captured pdf:", saved, "| url:", resp.url)
                        out.append({"source": "network", "pdf_url": resp.url, "saved": saved})
                    except Exception as e:
                        print("[playwright] failed to save network response:", e)
            else:
                print("[playwright] no PDF network responses captured after clicks/waits.")

    except Exception:
        traceback.print_exc()
    finally:
        # cleanup Playwright resources
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if p:
                p.stop()
        except Exception:
            pass

    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python discover/playwright_fetch_pdf.py \"CompanyName\" \"https://...\"")
        sys.exit(1)
    company = sys.argv[1]
    url = sys.argv[2]
    try:
        results = fetch_pdf_via_playwright(company, url, headless=True)
        if not results:
            print("No PDFs were downloaded by Playwright for this page.")
        else:
            print("\nDownloaded PDFs:")
            for r in results:
                print(r)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
