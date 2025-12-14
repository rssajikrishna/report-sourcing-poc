#!/usr/bin/env python3
"""
poc_discover_cached.py (Report Sourcing) — discovery + caching + PDF extraction & scoring

Usage (from project root):
  python discover/poc_discover_cached.py "Apple"
  python discover/poc_discover_cached.py "Apple" --force
  python discover/discover_cached.py "Apple" --ttl=3

Deps: requests, beautifulsoup4
Install: pip install requests beautifulsoup4
"""
from __future__ import annotations

import sys
import time
import re
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
import requests
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup

# ---------- CONFIG ----------
HEADERS = {"User-Agent": "ReportSourcing-POC/1.0 (+you@example.com)"}
IR_KEYWORDS = [
    "investor", "investors", "investor-relations", "investor_relations",
    "investorrelations", "financials", "reports", "annual", "ir", "sec", "edgar"
]
SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_PATH = SCRIPT_DIR / "discover_cache.json"
DEFAULT_TTL_DAYS = 7

PDF_MIN_BYTES = 2048  # minimum file size (HEAD check) to consider valid
PDF_SCORE_THRESHOLD = 0.60
SLEEP_BETWEEN_REQUESTS = 0.5
# ----------------------------


# ---------- caching helpers ----------
def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[cache] load failed:", e)
        return {}


def save_cache(cache: dict) -> None:
    tmp = str(CACHE_PATH) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(CACHE_PATH))
    except Exception as e:
        print("[cache] save failed:", e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def cache_key(company_name: str) -> str:
    return company_name.strip().lower()


def is_entry_stale(entry_ts_iso: str, ttl_days: int) -> bool:
    try:
        ts = datetime.fromisoformat(entry_ts_iso)
    except Exception:
        return True
    return datetime.utcnow() - ts > timedelta(days=ttl_days)


# ---------- discovery (DDG + homepage probe + probe paths) ----------
def ddg_search(query: str, max_results: int = 6, pause: float = 0.5):
    """
    Use DuckDuckGo HTML endpoint (lightweight) to get top results.
    Returns list of (title, url).
    """
    url = "https://html.duckduckgo.com/html/"
    try:
        r = requests.post(url, data={"q": query}, headers=HEADERS, timeout=12)
        r.raise_for_status()
    except Exception as e:
        print("[search] DuckDuckGo request failed:", e)
        return []
    time.sleep(pause)
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        # direct http(s)
        if href.startswith("http"):
            results.append((text, href))
        # ddg uses uddg=encoded_url sometimes
        elif "uddg=" in href:
            m = re.search(r"uddg=(http[^&]+)", href)
            if m:
                try:
                    decoded = unquote(m.group(1))
                    results.append((text, decoded))
                except Exception:
                    results.append((text, m.group(1)))
        if len(results) >= max_results:
            break
    return results


def scan_page_for_ir_links(base_url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        anchor = a.get_text(" ", strip=True)
        full = urljoin(base_url, href)
        score = 0
        text = (anchor + " " + full).lower()
        for k in IR_KEYWORDS:
            if k in text:
                score += 10
        path = urlparse(full).path.lower()
        for k in IR_KEYWORDS:
            if k in path:
                score += 3
        if score > 0:
            candidates.append((score, full, anchor))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates


def probe_common_paths(domain_root: str):
    paths = [
        "/investors", "/investor", "/investor-relations", "/investor-relations/financials",
        "/investors/reports", "/investors/financials", "/reports", "/financials", "/ir", "/about/investors"
    ]
    found = []
    for p in paths:
        url = domain_root.rstrip("/") + p
        try:
            r = requests.head(url, headers=HEADERS, timeout=8, allow_redirects=True)
            if r.status_code < 400:
                rr = requests.get(url, headers=HEADERS, timeout=10)
                if rr.status_code == 200:
                    cs = scan_page_for_ir_links(url, rr.text)
                    score = 5 if cs else 1
                    found.append((score, url, "probe_path"))
        except Exception:
            continue
    return found


# ---------- sitemap probing (light) ----------
def fetch_sitemap_urls(root: str):
    """Try root/sitemap.xml and root/sitemap_index.xml, return list of urls found"""
    urls = []
    candidates = [urljoin(root, "sitemap.xml"), urljoin(root, "sitemap_index.xml")]
    for s in candidates:
        try:
            r = requests.get(s, headers=HEADERS, timeout=8)
            if r.status_code != 200:
                continue
            # find <loc> tags
            for m in re.findall(r"<loc>(.*?)</loc>", r.text, flags=re.I | re.S):
                if m:
                    urls.append(m.strip())
        except Exception:
            continue
        time.sleep(SLEEP_BETWEEN_REQUESTS)
    # dedupe while preserving order
    return list(dict.fromkeys(urls))


# ---------- PDF extraction & scoring ----------
PDF_FILENAME_PAT = re.compile(r"(10[- ]?k|10[- ]?q|annual|quarter|q[1-4]|fy|half|interim|report)", re.I)
YEAR_PAT = re.compile(r"(20\d{2})")


def extract_pdf_links(page_url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if ".pdf" in href.lower():
            full = urljoin(page_url, href)
            anchor = a.get_text(" ", strip=True)
            out.append((full, anchor))
    return out


def head_check_pdf(url: str):
    """HEAD the URL, return (is_pdf, content_length, final_url)"""
    headers = HEADERS.copy()
    try:
        r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        if r.status_code >= 400:
            return False, None, None
        ctype = r.headers.get("Content-Type", "")
        clen = r.headers.get("Content-Length")
        try:
            clen = int(clen) if clen else None
        except Exception:
            clen = None
        is_pdf = "pdf" in (ctype or "").lower() or url.lower().endswith(".pdf")
        return is_pdf, clen, r.url
    except Exception:
        return False, None, None


def score_pdf_candidate(pdf_url: str, anchor_text: str, candidate_page_url: str, sitemap_urls: list):
    """
    Compute a score (0..1) for the pdf link based on heuristics:
    - url/path contains IR keywords
    - anchor text contains report keywords
    - filename contains report keywords
    - found in sitemap
    - year tokens
    """
    score = 0.0
    reason = {}
    lowtext = (anchor_text or "") + " " + (pdf_url or "")
    lowtext = lowtext.lower()
    # url path signal
    s_url = 0.30 if any(k in urlparse(pdf_url).path.lower() for k in IR_KEYWORDS) else 0.0
    score += s_url
    reason['s_url'] = s_url
    # anchor signal
    s_anchor = 0.25 if re.search(PDF_FILENAME_PAT, anchor_text or "") else 0.0
    score += s_anchor
    reason['s_anchor'] = s_anchor
    # filename signal
    fname = os.path.basename(urlparse(pdf_url).path or "")
    s_fname = 0.20 if re.search(PDF_FILENAME_PAT, fname) else 0.0
    score += s_fname
    reason['s_fname'] = s_fname
    # sitemap signal
    s_sitemap = 0.15 if any(pdf_url.startswith(u) or u in pdf_url for u in (sitemap_urls or [])) else 0.0
    score += s_sitemap
    reason['s_sitemap'] = s_sitemap
    # year signal (bonus)
    y = YEAR_PAT.search(lowtext)
    s_year = 0.10 if y else 0.0
    score += s_year
    reason['s_year'] = s_year
    # cap
    final = min(1.0, score)
    reason['final'] = final
    # doc type detection
    doc_type = "unknown"
    at = (anchor_text or "") + " " + fname
    atl = at.lower()
    if re.search(r"(10[- ]?k|annual report|annual)", atl):
        doc_type = "annual"
    elif re.search(r"\bq([1-4])\b", atl) or re.search(r"quarter", atl):
        doc_type = "quarterly"
    elif re.search(r"\bhalf\b|\bh1\b|\bh2\b|half[- ]?year", atl):
        doc_type = "half"
    elif re.search(r"interim", atl):
        doc_type = "interim"
    # year extraction
    year = None
    ym = YEAR_PAT.search(atl)
    if ym:
        try:
            year = int(ym.group(1))
        except Exception:
            year = None
    return final, reason, doc_type, year, fname


# ---------- main discovery + pdf-scoring flow ----------
def find_ir_candidates_fresh(company_name: str):
    query = f"{company_name} investor relations"
    print(f"[discover] searching: {query}")
    candidates = []
    results = ddg_search(query)
    # convert SERP to candidates
    for title, url in results:
        low = (title + " " + url).lower()
        added = False
        for k in IR_KEYWORDS:
            if k in low:
                candidates.append((0.9, url, "serp"))
                added = True
                break
        if not added:
            candidates.append((0.3, url, "serp_fallback"))

    sitemap_urls = []  # ensure defined for later use

    # probe homepage / paths
    if results:
        top_url = results[0][1]
        parsed = urlparse(top_url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        try:
            r = requests.get(root, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                scanned = scan_page_for_ir_links(root, r.text)
                for sc in scanned:
                    conf = min(0.8, 0.4 + sc[0] / 50.0)
                    candidates.append((conf, sc[1], "homepage_scan"))
        except Exception:
            pass

        found_paths = probe_common_paths(root)
        for sc in found_paths:
            candidates.append((0.5, sc[1], sc[2]))

        # sitemap urls (light)
        try:
            sitemap_urls = fetch_sitemap_urls(root)
            if sitemap_urls:
                # prefer direct pdfs found in sitemap as top candidates
                for u in sitemap_urls:
                    if u.lower().endswith(".pdf"):
                        candidates.append((0.95, u, "sitemap_pdf"))
                    else:
                        # if path contains IR keywords
                        if any(k in u.lower() for k in IR_KEYWORDS):
                            candidates.append((0.75, u, "sitemap_page"))
        except Exception:
            sitemap_urls = []
    else:
        sitemap_urls = []

    # dedupe by url -> dict to preserve highest confidence
    seen = {}
    for conf, url, method in candidates:
        u = url.split("#")[0].rstrip("/")
        if u in seen:
            if conf > seen[u][0]:
                seen[u] = (conf, method)
        else:
            seen[u] = (conf, method)

    # build ordered list and then for each candidate extract pdfs and score them
    ordered = []
    for u, (conf, method) in seen.items():
        ordered.append({
            "confidence": float(conf),
            "url": u,
            "method": method,
            "discovered_at": datetime.utcnow().isoformat() + "Z"
        })
    ordered.sort(key=lambda x: (-x["confidence"], x["url"]))

    # Now scan each candidate page for PDFs and score them; attach pdfs list to candidate
    for cand in ordered:
        cand_url = cand["url"]
        cand["pdfs"] = []
        try:
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            r = requests.get(cand_url, headers=HEADERS, timeout=12)
            # if we got HTML content, extract anchors
            if r.status_code == 200 and r.headers.get("Content-Type", "").lower().find("text") != -1:
                html = r.text
                pdfs = extract_pdf_links(cand_url, html)
                # attempt scoring each pdf
                for pdf_url, anchor in pdfs:
                    # perform a light HEAD to validate
                    is_pdf, clen, final = head_check_pdf(pdf_url)
                    # include sitemap hits as extra evidence
                    s_urls = sitemap_urls or []
                    score, reason, doc_type, year, fname = score_pdf_candidate(pdf_url, anchor, cand_url, s_urls)
                    cand["pdfs"].append({
                        "score": score,
                        "reason": reason,
                        "doc_type": doc_type,
                        "year": year,
                        "anchor": anchor,
                        "pdf_url": pdf_url,
                        "final_url": final or pdf_url,
                        "content_length": clen,
                        "head_is_pdf": is_pdf
                    })
            else:
                # could be a direct pdf candidate (e.g., sitemap gave direct PDF) or non-HTML response
                if cand_url.lower().endswith(".pdf"):
                    pdf_url = cand_url
                    is_pdf, clen, final = head_check_pdf(pdf_url)
                    s_urls = sitemap_urls or []
                    score, reason, doc_type, year, fname = score_pdf_candidate(pdf_url, "", cand_url, s_urls)
                    cand["pdfs"].append({
                        "score": score,
                        "reason": reason,
                        "doc_type": doc_type,
                        "year": year,
                        "anchor": "",
                        "pdf_url": pdf_url,
                        "final_url": final or pdf_url,
                        "content_length": clen,
                        "head_is_pdf": is_pdf
                    })
        except Exception as e:
            # skip pdf extraction on errors but keep candidate
            cand.setdefault("errors", []).append(str(e))
            continue
        # sort pdfs by score desc, then year desc
        cand["pdfs"].sort(key=lambda x: (-x["score"], -(x.get("year") or 0)))

    return ordered


# -------------- caching wrapper around discovery --------------
def find_ir_candidates(company_name: str, ttl_days: int = DEFAULT_TTL_DAYS, force_refresh: bool = False):
    cache = load_cache()
    key = cache_key(company_name)
    if not force_refresh and key in cache:
        entry = cache[key]
        if not is_entry_stale(entry.get("cached_at", ""), ttl_days):
            print(f"[cache] returning cached candidates for '{company_name}' (cached_at={entry.get('cached_at')})")
            return entry.get("candidates", [])
        else:
            print(f"[cache] cached entry stale for '{company_name}' (cached_at={entry.get('cached_at')})")
    # Otherwise, compute fresh candidates
    fresh = find_ir_candidates_fresh(company_name)
    # store in cache
    cache[key] = {
        "company": company_name,
        "cached_at": datetime.utcnow().isoformat() + "Z",
        "candidates": fresh
    }
    try:
        save_cache(cache)
    except Exception as e:
        print("[cache] save failed:", e)
    return fresh


# ---------- CLI helper ----------
def cli(name: str, ttl_days: int = DEFAULT_TTL_DAYS, force: bool = False):
    cands = find_ir_candidates(name, ttl_days=ttl_days, force_refresh=force)
    if not cands:
        print("No candidates found.")
        return
    print("\nTop candidates (confidence, method, url, discovered_at):")
    for c in cands[:8]:
        print(f"{c['confidence']:.2f}\t{c['method']}\t{c['url']}\t{c['discovered_at']}")
        if c.get("pdfs"):
            print("  PDFs found (score, type, year, head_ok, size):")
            for p in c["pdfs"][:8]:
                print(f"    {p['score']:.2f}\t{p['doc_type']}\t{p.get('year')}\t{p['head_is_pdf']}\t{p['content_length']}")
                print(f"      {p['pdf_url']}")
    # print a short fast-path top PDF across candidates
    top_pdf = None
    for c in cands:
        for p in c.get("pdfs", []):
            if not top_pdf or (p['score'] > top_pdf['score'] or (p['score'] == top_pdf['score'] and (p.get('year') or 0) > (top_pdf.get('year') or 0))):
                top_pdf = p
    if top_pdf:
        print("\nTop PDF (fast-path):")
        print(f"  score={top_pdf['score']:.2f} type={top_pdf['doc_type']} year={top_pdf.get('year')} url={top_pdf['pdf_url']}")
        if top_pdf['score'] >= PDF_SCORE_THRESHOLD:
            print("  --> This PDF meets the fast-path score threshold.")
        else:
            print("  --> Top PDF did NOT meet fast-path threshold; consider Playwright fallback for higher confidence.")
    else:
        print("\nNo PDFs located in top candidates. Consider --force or Playwright fallback.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python poc_discover_cached.py \"Company Name\" [--force] [--ttl DAYS]")
        sys.exit(1)
    name_args = []
    force = False
    ttl = DEFAULT_TTL_DAYS
    # simple arg parsing
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--force":
            force = True
            i += 1
        elif a.startswith("--ttl"):
            if "=" in a:
                try:
                    ttl = int(a.split("=", 1)[1])
                except Exception:
                    ttl = DEFAULT_TTL_DAYS
                i += 1
            else:
                # next arg should be days
                try:
                    ttl = int(sys.argv[i + 1])
                    i += 2
                except Exception:
                    ttl = DEFAULT_TTL_DAYS
                    i += 1
        else:
            name_args.append(a)
            i += 1
    name = " ".join(name_args)
    cli(name, ttl_days=ttl, force=force)
