# Report Sourcing Automation – POC

A Proof-of-Concept system that automatically discovers, downloads, deduplicates, classifies, and serves **financial reports (Annual, Quarterly, Half-Yearly, Q3, etc.)** for multiple companies.
Includes: discovery pipeline, PDF fetcher, metadata extraction, backend storage, and a simple UI for browsing reports.

---

## 🚀 **Objective**

To automatically monitor selected companies' investor-relations websites, locate newly published PDF reports, download them with metadata, prevent duplicates using SHA-256, store them in a backend database, and provide a UI where a user can:

* Select a company
* Select report type (Annual, Q1, Q2, Q3, Q4, Half-Yearly)
* View / Download available reports

This POC demonstrates reliable report sourcing with optional AI enhancement later.

---

## 🧠 **Key Features**

### 🔍 **1. Report Discovery**

* Static HTML scanning (regex + heuristics)
* Sitemap probing
* Keyword-based scoring (annual, Q3, FY, 10-K, 10-Q, etc.)
* Fast-path detection for high-confidence PDF links

### 🎭 **2. Playwright Fallback (Dynamic Pages)**

Used only when:

* PDFs load via JavaScript
* Buttons must be clicked
* Cookie banners hide content

Captures:

* Anchor-based PDF downloads
* Network-level PDF responses

### 📥 **3. Ingestion & Deduplication**

* Downloads each PDF
* Computes **SHA-256** hash to detect duplicates
* Stores metadata in SQLite (company, year, doc type, URL, bytes)
* Normalizes filenames and saves to `storage/<CompanyName>/`

### 🗄 **4. Backend (FastAPI + SQLite)**

* API for listing companies & documents
* Serves stored PDF files
* Easy integration with UI

### 🖥️ **5. UI (Streamlit)**

* User selects **Company**
* User selects **Document Type**
* Lists PDFs with preview/download links
* Shows metadata (year, type, score, ingestion time)

---

## 🏗️ **System Architecture**

```
+------------------+         +----------------------------+
|  Discovery Layer |  --->   |  High-confidence PDFs      |
|  (poc_discover)  |         |  (no browser needed)       |
+------------------+         +-------------+--------------+
                                              |
                                              v
                +------------------+   if none found  +------------------------+
                |    Pipeline      |  ----------------> | Playwright Fallback  |
                |  (pipeline.py)   |                    | (browser fetch)      |
                +--------+---------+                    +---------+------------+
                         |                                        |
                         v                                        v
                +----------------+                        +----------------+
                |  Monitor       | <--downloads-- PDFs -->|  Storage       |
                | (monitor.py)   |                        | /storage/...   |
                +----------------+                        +----------------+
                         |
                         v
                +----------------+
                |   SQLite DB    |
                | (models.py)    |
                +----------------+
                         |
                         v
              +-----------------------+
              | Streamlit UI / API    |
              +-----------------------+
```

---

## 📂 **Project Structure**

```
project/
│
├── discover/
│   ├── poc_discover_cached.py         # Static discovery & scoring
│   ├── playwright_fetch_pdf.py        # Playwright fallback for dynamic pages
│
├── storage/                           # Saved PDF files (auto-created)
│
├── db/
│   ├── reports.db                     # SQLite database
│
├── models.py                          # Database models (Company, Document)
├── monitor.py                         # Periodic ingestion worker
├── pipeline.py                        # Orchestrator: Discover → Fallback
├── ui_streamlit.py                    # User interface (Streamlit)
│
└── README.md                          # This file
```

---

## ⚙️ **Installation**

### 1. Clone repository

```bash
git clone https://github.com/yourusername/report-sourcing-poc.git
cd report-sourcing-poc
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

For Playwright:

```bash
pip install playwright
python -m playwright install
```

---

## ▶️ **Usage**

### **1. Add a company to the database**

Using the FastAPI backend or manually inserting:

```bash
curl -X POST "http://localhost:8000/companies" \
  -H "Content-Type: application/json" \
  -d '{"name":"CompanyA","domain":"companyA.com","investor_url":"https://companyA.com/investors"}'
```

---

### **2. Run discovery**

```bash
python discover/poc_discover_cached.py "CompanyA"
```

---

### **3. Run pipeline (discovery → fallback)**

```bash
python pipeline.py "CompanyA"
```

---

### **4. Run ingestion worker**

```bash
python monitor.py
```

---

### **5. Run UI**

```bash
streamlit run ui_streamlit.py
```

Open in browser:
👉 [http://localhost:8501](http://localhost:8501)

---

## 🔒 **Deduplication Logic (SHA-256)**

Two files are considered identical if:

```
sha256(fileA) == sha256(fileB)
```

This prevents:

* Duplicate documents in DB
* Re-download of same report
* Storing same PDF with different filenames

---

## 🧪 **Examples of Reports Detected**

* Annual Report 2024
* Quarterly Report Q3 2025
* Half-Yearly H1 2025
* Form 10-K / 10-Q (SEC filings)
* Sustainability / ESG reports

---

## 🧩 **Future Enhancements**

Optional AI integration:

* LLM-based report classification
* Financial metadata extraction
* Auto-summaries for Fitch analysts
* Change detection between versions
* Vector search (semantic report search)

---

## 📝 **License**

MIT License 

---
