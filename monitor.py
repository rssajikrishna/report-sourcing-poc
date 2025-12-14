# monitor.py (patched)
import os
import re
import requests
import hashlib
import json
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from sqlalchemy.orm import sessionmaker
# IMPORTANT: import from your models module (not backend)
from models import Company, Document, engine as BACKEND_ENGINE, Base
import datetime

# re-use backend DB engine
engine = BACKEND_ENGINE
Session = sessionmaker(bind=engine)

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

# simple doc type rules
DOC_RULES = [
    (re.compile(r"(annual report|10-k|10k|annual)", re.I), "Annual"),
    (re.compile(r"(10-q|10q|quarter|q[1-4])", re.I), "Quarterly"),
    (re.compile(r"(h1|h2|half[- ]?year|half yearly)", re.I), "Half-Yearly"),
    (re.compile(r"(q3|third quarter|3rd quarter)", re.I), "Q3"),
]

def detect_doc_type(text):
    t = (text or "").lower()
    for pat, label in DOC_RULES:
        if pat.search(t):
            if label == "Quarterly" and re.search(r"\bq3\b", t):
                return "Q3"
            return label
    return "Unknown"

def sha256_bytes(b: bytes) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def extract_pdf_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().endswith(".pdf") or ".pdf?" in href.lower():
            links.append({"url": urljoin(base, href), "text": a.get_text(" ", strip=True)})
    return links

def download_and_store(url, suggested_name=None):
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        content = r.content
        h = sha256_bytes(content)
        name = suggested_name or os.path.basename(urlparse(url).path) or f"doc_{h[:8]}.pdf"
        safe = re.sub(r'[^A-Za-z0-9._-]', '_', name)
        dest = os.path.join(STORAGE_DIR, safe)
        # collision avoidance
        if os.path.exists(dest):
            base, ext = os.path.splitext(dest)
            dest = f"{base}_{h[:8]}{ext}"
        with open(dest, "wb") as f:
            f.write(content)
        return dest, h, len(content)
    except Exception as e:
        print("Download error", url, e)
        return None, None, None

def run_once():
    session = Session()
    companies = session.query(Company).all()
    for c in companies:
        print("Checking", c.name, c.investor_url)
        try:
            r = requests.get(c.investor_url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print("Failed to fetch", c.investor_url, e)
            continue
        links = extract_pdf_links(r.text, c.investor_url)
        for link in links:
            url = link["url"]
            anchor = link["text"]
            # naive dedupe: check if same source_url already in DB
            exists = session.query(Document).filter(Document.source_url == url).first()
            if exists:
                print("  already recorded (same URL)", url)
                continue
            # download
            dest, sha, size = download_and_store(url)
            if not dest:
                continue
            doc_type = detect_doc_type(anchor + " " + os.path.basename(dest))
            fiscal_year = None
            m = re.search(r"(20\d{2})", anchor)
            if m:
                fiscal_year = int(m.group(1))
            # store actual filesystem path returned by download_and_store
            storage_path = dest
            doc = Document(
                company_id=c.id,
                filename=os.path.basename(dest),
                storage_path=storage_path,
                source_url=url,
                sha256=sha,
                document_type=doc_type,
                fiscal_year=fiscal_year,
                pages=None,
                extra_metadata={"anchor": anchor, "size": size},
                ingested_at=datetime.datetime.utcnow()
            )
            try:
                session.add(doc)
                session.commit()
                print("  + ingested:", doc.filename, "type:", doc.document_type)
            except Exception as e:
                session.rollback()
                print("  db commit failed for", url, e)
    session.close()

if __name__ == "__main__":
    # one-shot run. To schedule, use cron or run this script in a loop with sleep.
    run_once()
