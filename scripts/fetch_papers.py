#!/usr/bin/env python3
"""
Paper Monitor - Small Room Acoustics Research Tracker
Fetches and filters papers related to small room acoustics.
"""

import json
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import feedparser
import requests
import ssl
import urllib.request
from dateutil import parser as date_parser

# Proxy & SSL setup for local testing behind VPN
REQUESTS_PROXIES = {}
if os.environ.get("HTTP_PROXY"):
    REQUESTS_PROXIES["http"] = os.environ["HTTP_PROXY"]
if os.environ.get("HTTPS_PROXY"):
    REQUESTS_PROXIES["https"] = os.environ["HTTPS_PROXY"]
REQUESTS_VERIFY = os.environ.get("REQUESTS_VERIFY", "true").lower() != "false"


def fetch_url(url: str, headers: dict = None, timeout: int = 30) -> bytes:
    """Fetch URL with requests, fallback to urllib on SSL errors."""
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, proxies=REQUESTS_PROXIES, verify=REQUESTS_VERIFY)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        if "SSL" in str(type(e).__name__) or "SSLError" in str(e) or "EOF" in str(e):
            req = urllib.request.Request(url, headers=headers or {})
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handler = urllib.request.HTTPSHandler(context=ctx)
            opener = urllib.request.build_opener(handler)
            with opener.open(req, timeout=timeout) as response:
                return response.read()
        raise


ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "papers.json"

CROSSREF_HEADERS = {
    "User-Agent": "PaperMonitor/1.0 (mailto:paper-monitor@example.com)",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Journal configurations
# ---------------------------------------------------------------------------

JOURNALS_RSS = {
    "jsv": {
        "name": "Journal of Sound and Vibration",
        "short": "JSV",
        "rss": "https://rss.sciencedirect.com/publication/science/0022460X",
        "website": "https://www.sciencedirect.com/journal/journal-of-sound-and-vibration",
        "filter_type": "jsv",
    },
    "applied_acoustics": {
        "name": "Applied Acoustics",
        "short": "App. Acoustics",
        "rss": "https://rss.sciencedirect.com/publication/science/0003682X",
        "website": "https://www.sciencedirect.com/journal/applied-acoustics",
        "filter_type": "applied_acoustics",
    },
}

JOURNALS_CROSSREF = {
    "jasa": {
        "name": "Journal of the Acoustical Society of America",
        "short": "JASA",
        "issn": "0001-4966",
        "website": "https://pubs.aip.org/jasa",
        "filter_type": "jasa",
    },
    "aes": {
        "name": "Journal of the Audio Engineering Society",
        "short": "AES",
        "issn": "1549-4950",
        "website": "https://www.aes.org/journal/",
        "filter_type": "aes",
    },
}

ARXIV_QUERY = "http://export.arxiv.org/api/query?search_query=cat:cs.SD+OR+cat:eess.AS&sortBy=submittedDate&sortOrder=descending&max_results=100"

# ---------------------------------------------------------------------------
# Date threshold
# ---------------------------------------------------------------------------
MIN_YEAR = 2000

# ---------------------------------------------------------------------------
# Unified filtering keywords
# ---------------------------------------------------------------------------

# Any of these in title/abstract -> strongly suggests indoor/small-room acoustics
INDOOR_KEYWORDS = [
    # Room types
    "room acoustics", "architectural acoustics", "indoor acoustics",
    "small room", "small enclosure", "small enclosed space", "small cavity",
    "car cabin", "vehicle cabin", "automotive interior", "automotive cabin",
    "listening room", "control room", "recording studio", "home studio",
    "home theater", "domestic room", "project studio", "bedroom studio",
    "vocal booth", "anechoic chamber", "reverberation chamber",
    # Phenomena & metrics
    "reverberation", "reverberant", "reverberation time", "rt60", "t60", "edt",
    "room impulse response", "rir", "room transfer function",
    "room mode", "modal analysis", "modal density", "modal overlap",
    "standing wave", "schroeder frequency",
    "absorption coefficient", "sound absorption", "surface absorption",
    "acoustic diffusion", "diffuser", "diffusers", "diffusion",
    "wall absorption", "ceiling absorption", "floor absorption",
    "low frequency", "low-frequency", "bass response", "bass trap",
    "sound field", "acoustic field", "sound field in room",
    "noise reduction", "noise control", "sound insulation",
    "acoustic treatment", "room conditioning", "acoustic panel",
    # Measurement & perception
    "acoustic measurement", "room measurement", "acoustic environment",
    "acoustic quality", "spatial audio", "spatial hearing", "binaural",
    "psychoacoustics", "speech intelligibility", "speech perception",
]

# Exclusion keywords -> drop paper if any appear in title/abstract
EXCLUDE_KEYWORDS = [
    "underwater", "acoustical oceanography", "oceanography",
    "acoustic metamaterial", "metamaterial",
    "animal bioacoustics", "biomedical acoustics",
    "ultrasound imaging", "medical ultrasound",
    "sonar", "seismic", "marine mammal", "bat echolocation",
    "acoustic black hole", "black hole",
    "structural acoustics and vibration",
]

# Special per-journal title keywords (title-only, fast pre-filter)
TITLE_QUICK_INCLUDE = {
    "jsv": [
        "small enclosure", "small enclosed space", "small room",
        "small cavity", "car cabin", "automotive", "vehicle cabin",
        "room acoustic", "room mode", "modal analysis",
        "room impulse response", "reverberation",
    ],
    "applied_acoustics": [
        "room acoustics", "architectural acoustics", "sound perception",
        "acoustic treatment", "room measurement", "sound field",
        "car", "headrest", "localization", "cabin", "automotive", "vehicle",
        "small room", "small enclosure", "studio", "listening room",
        "reverberation", "absorption", "diffusion",
    ],
    "aes": [
        "small enclosure", "small enclosed space", "small room",
        "small cavity", "car cabin", "automotive", "vehicle cabin",
        "studio", "control room", "listening room", "home theater",
        "room acoustic", "room mode", "reverberation", "absorption",
        "diffusion", "sound field", "spatial audio", "binaural",
    ],
}


def contains_any(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    for kw in keywords:
        if re.search(r'\b' + re.escape(kw.lower()) + r'\b', text_lower):
            return True
    return False


def is_excluded(text: str, exclude_list: list[str]) -> bool:
    return contains_any(text, exclude_list)


def filter_paper(paper: dict, filter_type: str) -> bool:
    """Return True if paper passes journal-specific filtering."""
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    combined = title + " " + abstract

    # Global exclusion first
    if is_excluded(combined, EXCLUDE_KEYWORDS):
        return False

    # ---- JASA ----
    if filter_type == "jasa":
        if contains_any(combined, INDOOR_KEYWORDS):
            return True
        return False

    # ---- Applied Acoustics ----
    if filter_type == "applied_acoustics":
        if contains_any(title, TITLE_QUICK_INCLUDE["applied_acoustics"]):
            return True
        if contains_any(combined, INDOOR_KEYWORDS):
            return True
        return False

    # ---- JSV ----
    if filter_type == "jsv":
        if contains_any(title, TITLE_QUICK_INCLUDE["jsv"]):
            return True
        return False

    # ---- AES ----
    if filter_type == "aes":
        if contains_any(title, TITLE_QUICK_INCLUDE["aes"]):
            return True
        return False

    # ---- Generic / arXiv / fallback ----
    if contains_any(combined, INDOOR_KEYWORDS):
        return True
    return False


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def clean_jats_abstract(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r'<jats:[^>]+>', '', text)
    text = re.sub(r'</jats:[^>]+>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    return normalize_text(text)


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


def year_from_date_str(date_str: str) -> int:
    """Extract year from ISO date string."""
    if not date_str:
        return 9999
    parts = date_str.split("-")
    try:
        return int(parts[0])
    except (ValueError, IndexError):
        return 9999


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_rss(journal_key: str, journal_config: dict) -> list[dict]:
    papers = []
    rss_url = journal_config["rss"]
    filter_type = journal_config.get("filter_type", "generic")
    print(f"  Fetching {journal_config['short']} from RSS ...")

    try:
        content = fetch_url(rss_url, headers={
            "User-Agent": "Mozilla/5.0 (PaperMonitor/1.0; Academic Research)"
        })
        feed = feedparser.parse(content)
    except Exception as e:
        print(f"    ✗ RSS fetch failed for {journal_config['short']}: {e}")
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
            m = re.search(r'10\.\d{4,}/[^\s"<>]+', link)
            if m:
                doi = m.group(0)

        pub_date = parse_date(entry)
        date_str = pub_date.strftime("%Y-%m-%d") if pub_date else ""

        # Date filter
        if year_from_date_str(date_str) < MIN_YEAR:
            continue

        summary = normalize_text(entry.get("summary", entry.get("description", "")))
        summary = re.sub(r'<[^>]+>', '', summary)

        paper = {
            "id": "",
            "title": title,
            "authors": authors_str,
            "affiliations": "",
            "abstract": summary,
            "journal": journal_config["short"],
            "journal_full": journal_config["name"],
            "doi": doi,
            "url": link,
            "published": date_str,
            "is_oa": False,
            "oa_url": "",
            "image_url": "",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Apply journal-specific filter
        if not filter_paper(paper, filter_type):
            continue

        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from {journal_config['short']}")
    return papers


def fetch_crossref(journal_key: str, journal_config: dict) -> list[dict]:
    papers = []
    issn = journal_config["issn"]
    filter_type = journal_config.get("filter_type", "generic")
    print(f"  Fetching {journal_config['short']} from CrossRef (ISSN: {issn}) ...")

    # Backdate to 2000, fetch up to 100 rows
    url = (
        f"https://api.crossref.org/works"
        f"?filter=issn:{issn},from-pub-date:{MIN_YEAR}-01-01"
        f"&sort=published&order=desc&rows=100"
    )
    try:
        content = fetch_url(url, headers=CROSSREF_HEADERS)
        data = json.loads(content)
    except Exception as e:
        print(f"    ✗ CrossRef fetch failed for {journal_config['short']}: {e}")
        return papers

    items = data.get("message", {}).get("items", [])
    for item in items:
        titles = item.get("title", [])
        if not titles:
            continue
        title = normalize_text(titles[0])
        if not title:
            continue

        # Authors + affiliations
        authors = []
        affiliations = []
        for author in item.get("author", []):
            given = author.get("given", "")
            family = author.get("family", "")
            if given and family:
                authors.append(f"{given} {family}")
            elif family:
                authors.append(family)
            elif given:
                authors.append(given)

            for aff in author.get("affiliation", []):
                name = aff.get("name", "")
                if name and name not in affiliations:
                    affiliations.append(name)

        authors_str = ", ".join(authors) if authors else "Unknown"
        affiliations_str = "; ".join(affiliations[:3]) if affiliations else ""

        # Date
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

        # Date filter (extra safety)
        if year_from_date_str(date_str) < MIN_YEAR:
            continue

        doi = item.get("DOI", "")
        url_link = item.get("URL", f"https://doi.org/{doi}" if doi else "")
        abstract = clean_jats_abstract(item.get("abstract", ""))

        # OA detection
        is_oa = False
        oa_url = ""
        for lic in item.get("license", []):
            start = lic.get("start", {}).get("date-time", "")
            if start:
                is_oa = True
        if "creativecommons" in str(item.get("license", [])).lower():
            is_oa = True

        paper = {
            "id": "",
            "title": title,
            "authors": authors_str,
            "affiliations": affiliations_str,
            "abstract": abstract,
            "journal": journal_config["short"],
            "journal_full": journal_config["name"],
            "doi": doi,
            "url": url_link,
            "published": date_str,
            "is_oa": is_oa,
            "oa_url": oa_url,
            "image_url": "",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Apply journal-specific filter
        if not filter_paper(paper, filter_type):
            continue

        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from {journal_config['short']}")
    return papers


def fetch_arxiv() -> list[dict]:
    papers = []
    print(f"  Fetching arXiv audio/acoustics ...")

    try:
        content = fetch_url(ARXIV_QUERY)
        root = ET.fromstring(content)
    except Exception as e:
        print(f"    ✗ arXiv fetch failed: {e}")
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

        # Date filter
        if year_from_date_str(date_str) < MIN_YEAR:
            continue

        # arXiv is always OA
        is_oa = True
        oa_url = link

        paper = {
            "id": "",
            "title": title,
            "authors": authors_str,
            "affiliations": "",
            "abstract": summary,
            "journal": "arXiv",
            "journal_full": "arXiv (Audio & Acoustics)",
            "doi": f"arXiv:{arxiv_id}" if arxiv_id else "",
            "url": link,
            "published": date_str,
            "is_oa": is_oa,
            "oa_url": oa_url,
            "image_url": "",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Apply generic filter
        if not filter_paper(paper, "generic"):
            continue

        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from arXiv")
    return papers


# ---------------------------------------------------------------------------
# Merge & persist
# ---------------------------------------------------------------------------

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

    # Merge
    merged = merge_papers(existing_papers, all_new_papers)

    # Re-apply filters to ALL papers (including existing ones) so old data
    # that no longer matches stricter rules gets cleaned out.
    print(f"  Re-filtering {len(merged)} total papers...")
    journal_filter_map = {k: v["filter_type"] for k, v in {**JOURNALS_RSS, **JOURNALS_CROSSREF}.items()}
    journal_filter_map["arXiv"] = "generic"

    def get_filter_type(paper):
        return journal_filter_map.get(paper.get("journal", ""), "generic")

    filtered = [p for p in merged if filter_paper(p, get_filter_type(p))]
    dropped = len(merged) - len(filtered)
    if dropped > 0:
        print(f"    Dropped {dropped} papers that no longer match filters")
    merged = filtered

    # Sort by date desc
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
