#!/usr/bin/env python3
"""
Paper Monitor - Fetch latest papers from acoustics journals
Supports: JASA, JSV, AES, Applied Acoustics, arXiv audio/acoustics
"""

import json
import hashlib
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import feedparser
import requests
from dateutil import parser as date_parser

# Paths
ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "papers.json"

# CrossRef polite user-agent (recommended by CrossRef)
CROSSREF_HEADERS = {
    "User-Agent": "PaperMonitor/1.0 (mailto:paper-monitor@example.com)",
    "Accept": "application/json",
}

# Journal configurations
JOURNALS_RSS = {
    "jsv": {
        "name": "Journal of Sound and Vibration",
        "short": "JSV",
        "rss": "https://rss.sciencedirect.com/publication/science/0022460X",
        "website": "https://www.sciencedirect.com/journal/journal-of-sound-and-vibration",
    },
    "applied_acoustics": {
        "name": "Applied Acoustics",
        "short": "App. Acoustics",
        "rss": "https://rss.sciencedirect.com/publication/science/0003682X",
        "website": "https://www.sciencedirect.com/journal/applied-acoustics",
    },
}

JOURNALS_CROSSREF = {
    "jasa": {
        "name": "Journal of the Acoustical Society of America",
        "short": "JASA",
        "issn": "0001-4966",
        "website": "https://pubs.aip.org/jasa",
    },
    "aes": {
        "name": "Journal of the Audio Engineering Society",
        "short": "AES",
        "issn": "1549-4950",
        "website": "https://www.aes.org/journal/",
    },
}

ARXIV_QUERY = "http://export.arxiv.org/api/query?search_query=cat:cs.SD+OR+cat:eess.AS&sortBy=submittedDate&sortOrder=descending&max_results=50"


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def clean_jats_abstract(text: Optional[str]) -> str:
    """Remove JATS XML tags from abstract text."""
    if not text:
        return ""
    # Remove common JATS tags
    text = re.sub(r'<jats:[^>]+>', '', text)
    text = re.sub(r'</jats:[^>]+>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return normalize_text(text)


def extract_doi(link: str) -> Optional[str]:
    match = re.search(r'10\.\d{4,}/[^\s"<>]+', link)
    return match.group(0) if match else None


def generate_id(paper: dict) -> str:
    if paper.get("doi"):
        return hashlib.sha256(paper["doi"].encode()).hexdigest()[:16]
    key = (paper.get("title", "") + paper.get("authors", "")).encode()
    return hashlib.sha256(key).hexdigest()[:16]


def parse_date(entry) -> Optional[datetime]:
    date_fields = ["published", "updated", "prism_publicationdate", "dc_date"]
    for field in date_fields:
        value = getattr(entry, field, None)
        if value:
            try:
                return date_parser.parse(value)
            except Exception:
                continue
    return None


def fetch_rss(journal_key: str, journal_config: dict) -> list[dict]:
    """Fetch papers from an RSS feed."""
    papers = []
    rss_url = journal_config["rss"]
    print(f"  Fetching {journal_config['short']} from RSS ...")

    try:
        response = requests.get(rss_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (PaperMonitor/1.0; Academic Research)"
        })
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as e:
        print(f"    ✗ Failed to fetch RSS for {journal_config['short']}: {e}")
        return papers

    for entry in feed.entries:
        title = normalize_text(entry.get("title", ""))
        if not title:
            continue

        authors = []
        if hasattr(entry, "authors"):
            authors = [a.get("name", "") for a in entry.authors if a.get("name")]
        elif hasattr(entry, "author"):
            authors = [entry.author]
        authors_str = ", ".join(authors) if authors else "Unknown"

        link = ""
        if entry.get("link"):
            link = entry.link
        elif hasattr(entry, "links") and entry.links:
            for l in entry.links:
                if l.get("type", "").startswith("text/html"):
                    link = l.get("href", "")
                    break
            if not link:
                link = entry.links[0].get("href", "")

        doi = entry.get("prism_doi") or entry.get("dc_identifier", "").replace("doi:", "")
        if not doi:
            doi = extract_doi(link) or ""

        pub_date = parse_date(entry)
        date_str = pub_date.strftime("%Y-%m-%d") if pub_date else ""

        summary = normalize_text(entry.get("summary", entry.get("description", "")))
        summary = re.sub(r'<[^>]+>', '', summary)

        paper = {
            "id": "",
            "title": title,
            "authors": authors_str,
            "abstract": summary,
            "journal": journal_config["short"],
            "journal_full": journal_config["name"],
            "doi": doi,
            "url": link,
            "published": date_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from {journal_config['short']}")
    return papers


def fetch_crossref(journal_key: str, journal_config: dict) -> list[dict]:
    """Fetch papers from CrossRef API by ISSN."""
    papers = []
    issn = journal_config["issn"]
    print(f"  Fetching {journal_config['short']} from CrossRef (ISSN: {issn}) ...")

    url = f"https://api.crossref.org/works?filter=issn:{issn}&sort=published&order=desc&rows=30"
    try:
        response = requests.get(url, timeout=30, headers=CROSSREF_HEADERS)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"    ✗ Failed to fetch CrossRef for {journal_config['short']}: {e}")
        return papers

    items = data.get("message", {}).get("items", [])
    for item in items:
        titles = item.get("title", [])
        if not titles:
            continue
        title = normalize_text(titles[0])
        if not title:
            continue

        # Authors
        authors = []
        for author in item.get("author", []):
            given = author.get("given", "")
            family = author.get("family", "")
            if given and family:
                authors.append(f"{given} {family}")
            elif family:
                authors.append(family)
            elif given:
                authors.append(given)
        authors_str = ", ".join(authors) if authors else "Unknown"

        # Date - prefer published-online, then published-print
        date_parts = None
        pub_online = item.get("published-online", {}).get("date-parts", [[]])[0]
        pub_print = item.get("published-print", {}).get("date-parts", [[]])[0]
        if pub_online and len(pub_online) >= 3:
            date_parts = pub_online
        elif pub_print and len(pub_print) >= 3:
            date_parts = pub_print
        elif pub_online and len(pub_online) >= 2:
            date_parts = pub_online + [1]
        elif pub_print and len(pub_print) >= 2:
            date_parts = pub_print + [1]
        elif pub_online and len(pub_online) >= 1:
            date_parts = pub_online + [1, 1]
        elif pub_print and len(pub_print) >= 1:
            date_parts = pub_print + [1, 1]

        date_str = ""
        if date_parts and len(date_parts) >= 3:
            try:
                date_str = f"{date_parts[0]:04d}-{date_parts[1]:02d}-{date_parts[2]:02d}"
            except Exception:
                pass
        elif date_parts and len(date_parts) >= 2:
            try:
                date_str = f"{date_parts[0]:04d}-{date_parts[1]:02d}"
            except Exception:
                pass
        elif date_parts and len(date_parts) >= 1:
            try:
                date_str = f"{date_parts[0]:04d}"
            except Exception:
                pass

        doi = item.get("DOI", "")
        url_link = item.get("URL", f"https://doi.org/{doi}" if doi else "")
        abstract = clean_jats_abstract(item.get("abstract", ""))

        paper = {
            "id": "",
            "title": title,
            "authors": authors_str,
            "abstract": abstract,
            "journal": journal_config["short"],
            "journal_full": journal_config["name"],
            "doi": doi,
            "url": url_link,
            "published": date_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from {journal_config['short']}")
    return papers


def fetch_arxiv() -> list[dict]:
    """Fetch recent audio/acoustics papers from arXiv."""
    papers = []
    print(f"  Fetching arXiv audio/acoustics ...")

    try:
        response = requests.get(ARXIV_QUERY, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as e:
        print(f"    ✗ Failed to fetch arXiv: {e}")
        return papers

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for entry in root.findall("atom:entry", ns):
        title = normalize_text(entry.findtext("atom:title", "", ns))
        if not title or title.lower().startswith("arxiv"):
            continue

        authors = []
        for author in entry.findall("atom:author", ns):
            name = author.findtext("atom:name", "", ns)
            if name:
                authors.append(name)
        authors_str = ", ".join(authors) if authors else "Unknown"

        summary = normalize_text(entry.findtext("atom:summary", "", ns))
        link = ""
        for l in entry.findall("atom:link", ns):
            if l.get("rel") == "alternate" and l.get("type") == "text/html":
                link = l.get("href", "")
                break
        if not link:
            link = entry.findtext("atom:id", "", ns)

        arxiv_id = ""
        id_text = entry.findtext("atom:id", "", ns)
        if id_text:
            match = re.search(r'arxiv\.org/abs/(.+)', id_text)
            if match:
                arxiv_id = match.group(1)

        published = entry.findtext("atom:published", "", ns)
        date_str = ""
        if published:
            try:
                date_str = date_parser.parse(published).strftime("%Y-%m-%d")
            except Exception:
                pass

        paper = {
            "id": "",
            "title": title,
            "authors": authors_str,
            "abstract": summary,
            "journal": "arXiv",
            "journal_full": "arXiv (Audio & Acoustics)",
            "doi": f"arXiv:{arxiv_id}" if arxiv_id else "",
            "url": link,
            "published": date_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from arXiv")
    return papers


def load_existing() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": "", "papers": []}


def merge_papers(existing: list[dict], new_papers: list[dict]) -> list[dict]:
    existing_ids = {p["id"] for p in existing}
    merged = list(existing)
    added = 0
    for paper in new_papers:
        if paper["id"] not in existing_ids:
            merged.append(paper)
            existing_ids.add(paper["id"])
            added += 1
    print(f"  Added {added} new papers, total {len(merged)}")
    return merged


def main():
    print(f"[{datetime.now().isoformat()}] Starting paper fetch...")

    data = load_existing()
    existing_papers = data.get("papers", [])
    print(f"  Existing papers: {len(existing_papers)}")

    all_new_papers = []

    # Fetch from RSS journals
    for key, config in JOURNALS_RSS.items():
        papers = fetch_rss(key, config)
        all_new_papers.extend(papers)

    # Fetch from CrossRef journals
    for key, config in JOURNALS_CROSSREF.items():
        papers = fetch_crossref(key, config)
        all_new_papers.extend(papers)

    # Fetch from arXiv
    all_new_papers.extend(fetch_arxiv())

    # Merge and sort
    merged = merge_papers(existing_papers, all_new_papers)
    merged.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Update data
    data["papers"] = merged
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Save
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().isoformat()}] Done. Total papers: {len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
