#!/usr/bin/env python3
"""
pipeline.py

Usage:
  python pipeline.py "Apple"

What it does:
- Loads discover/poc_discover_cached.py and calls find_ir_candidates(...)
- If any candidate contains PDF(s) with score >= PDF_SCORE_THRESHOLD, it prints them and exits.
- Otherwise calls discover/playwright_fetch_pdf.py's fetch_pdf_via_playwright(...) on the top candidate URL(s).
- Prints saved files (if any).
"""
from __future__ import annotations

import sys
import os
import traceback
import importlib.util
from pathlib import Path
from typing import Optional

# CONFIG — tune if you changed thresholds in poc_discover_cached.py
PDF_SCORE_THRESHOLD = 0.60

HERE = Path(__file__).resolve().parent
DISCOVER_MODULE_PATH = HERE / "discover" / "poc_discover_cached.py"
PLAYWRIGHT_MODULE_PATH = HERE / "discover" / "playwright_fetch_pdf.py"


def load_module_from_path(name: str, path: Path):
    path = Path(path)
    if not path.exists():
        raise ImportError(f"Module path does not exist: {path}")
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
    except Exception as e:
        raise ImportError(f"Failed to exec module {path}: {e}")
    return module


def choose_ttl(discover_module) -> int:
    # prefer discover.DEFAULT_TTL_DAYS if present
    try:
        return int(getattr(discover_module, "DEFAULT_TTL_DAYS", 7))
    except Exception:
        return 7


def main(company_name: str) -> int:
    if not DISCOVER_MODULE_PATH.exists():
        print("[pipeline] missing discover module:", DISCOVER_MODULE_PATH)
        return 2
    if not PLAYWRIGHT_MODULE_PATH.exists():
        print("[pipeline] missing playwright fallback module:", PLAYWRIGHT_MODULE_PATH)
        return 2

    # load discovery module
    try:
        discover = load_module_from_path("poc_discover_cached", DISCOVER_MODULE_PATH)
    except Exception as e:
        print("[pipeline] failed to load discovery module:", e)
        traceback.print_exc()
        return 3

    ttl_days = choose_ttl(discover)
    print(f"[pipeline] running discovery for '{company_name}' (cached, ttl_days={ttl_days}) ...")
    try:
        candidates = discover.find_ir_candidates(company_name, ttl_days=ttl_days, force_refresh=False)
        if not isinstance(candidates, list):
            print("[pipeline] discover.find_ir_candidates did not return a list.")
            return 4
    except Exception as e:
        print("[pipeline] discovery call failed:", e)
        traceback.print_exc()
        return 4

    if not candidates:
        print("[pipeline] discovery returned no candidates.")
    # inspect candidates for statically-found PDFs
    top_pdfs = []
    for cand in candidates:
        try:
            pdfs = cand.get("pdfs", []) if isinstance(cand, dict) else []
        except Exception:
            pdfs = []
        for p in pdfs:
            score = float(p.get("score", 0.0) or 0.0)
            head_ok = bool(p.get("head_is_pdf", False))
            size = int(p.get("content_length") or 0)
            if score >= PDF_SCORE_THRESHOLD and (head_ok or (size and size >= 2048)):
                top_pdfs.append((score, cand, p))

    if top_pdfs:
        top_pdfs.sort(key=lambda x: (-x[0], -(x[2].get("year") or 0)))
        print("[pipeline] static discovery found high-confidence PDF(s).")
        for score, cand, p in top_pdfs:
            print(f"  score={score:.2f} type={p.get('doc_type')} year={p.get('year')} head_ok={p.get('head_is_pdf')} size={p.get('content_length')}")
            print(f"    url: {p.get('pdf_url')}")
        return 0

    print("[pipeline] no high-confidence static PDFs found. Running Playwright fallback on top candidate(s).")

    TOP_CANDIDATES_TO_TRY = 2
    top_candidates = (candidates[:TOP_CANDIDATES_TO_TRY] if candidates else [])

    try:
        pw = load_module_from_path("playwright_fetch_pdf", PLAYWRIGHT_MODULE_PATH)
    except Exception as e:
        print("[pipeline] failed to load playwright module:", e)
        traceback.print_exc()
        return 5

    played_any = False
    saved_files = []
    for cand in top_candidates:
        url = cand.get("url") if isinstance(cand, dict) else None
        method = cand.get("method") if isinstance(cand, dict) else None
        if not url:
            print("[pipeline] skipping candidate with no url:", cand)
            continue
        print(f"[pipeline] Playwright trying candidate: {url} (method={method})")
        try:
            results = pw.fetch_pdf_via_playwright(company_name, url, headless=True)
            if results:
                played_any = True
                for r in results:
                    saved = r.get("saved")
                    saved_files.append(saved)
                    print(f"[pipeline] saved via playwright: {saved}  (source={r.get('source')}, url={r.get('pdf_url')})")
            else:
                print("[pipeline] playwright returned no PDFs for this candidate.")
        except Exception as e:
            print("[pipeline] playwright run failed for candidate:", e)
            traceback.print_exc()
            continue

    if not played_any:
        print("[pipeline] Playwright fallback did not find any PDFs. Consider manual review or force-refresh.")
        return 6

    print("[pipeline] Playwright saved files:")
    for s in saved_files:
        print("  ", s)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py \"Company Name\"")
        sys.exit(1)
    cname = " ".join(sys.argv[1:])
    rc = main(cname)
    sys.exit(rc)
