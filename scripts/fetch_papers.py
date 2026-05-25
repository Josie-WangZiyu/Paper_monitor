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
        # Fallback to urllib for SSL issues with certain proxies
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

ARXIV_QUERY = "http://export.arxiv.org/api/query?search_query=cat:cs.SD+OR+cat:eess.AS&sortBy=submittedDate&sortOrder=descending&max_results=100"

# ---------------------------------------------------------------------------
# Keyword-based relevance scoring for Small Room Acoustics
# ---------------------------------------------------------------------------

KEYWORD_GROUPS = {
    # Core small-room terms (highest weight)
    "core": {
        "weight": 3.0,
        "terms": [
            "small room acoustics", "small room acoustic",
            "small room measurement", "small room low frequency",
            "small room modal", "small-room acoustics", "small-room acoustic",
            "small enclosure acoustics", "small space acoustics",
            "small studio acoustics", "small listening room",
            "small control room", "small home theater",
            "small room sound", "small room response",
            "compact room acoustics", "compact room acoustic",
        ],
    },
    # Specific application scenes (high weight)
    "scene": {
        "weight": 2.5,
        "terms": [
            "car cabin acoustics", "car cabin acoustic", "vehicle cabin acoustics",
            "automotive acoustics", "automotive cabin", "vehicle interior acoustics",
            "automotive interior noise", "car interior acoustics",
            "small recording room", "small recording studio",
            "control room acoustics", "control room acoustic",
            "listening room acoustics", "listening room acoustic",
            "home theater acoustics", "home theater acoustic",
            "home studio acoustics", "home studio acoustic",
            "studio acoustics", "studio acoustic design",
            "domestic room acoustics", "domestic listening",
            "private room acoustics", "personal studio",
            "bedroom studio", "project studio",
            "booth acoustics", "vocal booth",
            "anechoic chamber", "reverberation chamber",
        ],
    },
    # Modal / low-frequency behavior (high weight)
    "modal": {
        "weight": 2.5,
        "terms": [
            "room mode", "room modes", "modal behavior", "modal analysis",
            "modal density", "modal distribution", "modal overlap",
            "modal frequency", "modal response", "modal decay",
            "modal damping", "modal prediction",
            "standing wave", "standing waves",
            "schroeder frequency", "schroeder-frequency",
            "low frequency room", "low-frequency room",
            "low frequency acoustic", "low-frequency acoustic",
            "bass response", "bass absorption", "bass trap", "bass traps",
            "room resonance", "acoustic resonance",
            "enclosure resonance", "cavity resonance",
            "helmholtz resonator", "membrane absorber",
            "porous absorber", "resonant absorber",
        ],
    },
    # Measurement & treatment (medium weight)
    "measurement": {
        "weight": 1.5,
        "terms": [
            "room acoustic measurement", "room acoustic measurements",
            "room impulse response", "room transfer function",
            "rir measurement", "rir acquisition",
            "room acoustic treatment", "acoustic treatment",
            "room acoustic design", "room acoustic optimization",
            "room acoustic simulation", "room acoustic modelling",
            "sound field in room", "sound field in small",
            "reverberation time", "rt60", "t60", "edt",
            "decay time", "early decay time",
            "diffuser", "diffusers", "acoustic diffusion", "binary diffuser", "schroeder diffuser",
            "absorption coefficient", "sound absorption",
            "acoustic panel", "acoustic panels",
            "sound transmission", "sound insulation",
            "noise reduction", "noise control",
            "acoustic conditioning", "room conditioning",
        ],
    },
    # General room-acoustics terms (lower weight, need accumulation)
    "general": {
        "weight": 1.0,
        "terms": [
            "room acoustics", "room acoustic", "indoor acoustics",
            "enclosed space acoustics", "interior acoustics",
            "acoustic environment", "acoustic quality",
            "room sound field", "interior sound field", "acoustic field in room",
            "sound reflection", "sound scattering",
            "wall absorption", "ceiling absorption",
            "floor absorption", "surface absorption",
        ],
    },
    # Car/vehicle specific (medium weight)
    "vehicle": {
        "weight": 1.5,
        "terms": [
            "cabin noise", "cabin acoustics", "cabin acoustic", "car cabin noise",
            "vehicle noise", "vehicle acoustics",
            "automotive noise", "automotive sound",
            "interior noise", "interior acoustics",
            "powertrain noise", "road noise", "wind noise",
            "nvh", "noise vibration harshness",
        ],
    },
}

SCORE_THRESHOLD = 1.0  # Minimum score to keep a paper


def build_keyword_patterns():
    """Pre-compile regex patterns for all keywords."""
    patterns = {}
    for group_name, group in KEYWORD_GROUPS.items():
        compiled = []
        for term in group["terms"]:
            # Word-boundary matching, case-insensitive
            escaped = re.escape(term)
            pattern = re.compile(r'\b' + escaped + r'\b', re.IGNORECASE)
            compiled.append(pattern)
        patterns[group_name] = {
            "weight": group["weight"],
            "patterns": compiled,
        }
    return patterns


KEYWORD_PATTERNS = build_keyword_patterns()


def score_paper(title: str, abstract: str) -> tuple[float, list[str]]:
    """
    Score a paper's relevance to small room acoustics.
    Returns (score, list of matched keywords).
    Title matches count 2x compared to abstract matches.
    """
    text_title = (title or "").lower()
    text_abstract = (abstract or "").lower()
    text_combined = text_title + " " + text_abstract

    score = 0.0
    matched = []

    for group_name, group in KEYWORD_PATTERNS.items():
        weight = group["weight"]
        for pattern in group["patterns"]:
            # Count in title (2x weight)
            title_matches = len(pattern.findall(text_title))
            # Count in abstract (1x weight)
            abstract_matches = len(pattern.findall(text_abstract))

            if title_matches > 0 or abstract_matches > 0:
                # Only record keyword once
                keyword = pattern.pattern.replace(r'\b', '').replace('\\', '')
                if keyword not in matched:
                    matched.append(keyword)

                score += title_matches * weight * 2.0
                score += abstract_matches * weight

    # Bonus: if title explicitly contains "room" + "acoustic" + "small"
    if "room" in text_title and "acoustic" in text_title and ("small" in text_title or "car" in text_title):
        score += 1.5

    return score, matched


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


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_rss(journal_key: str, journal_config: dict) -> list[dict]:
    papers = []
    rss_url = journal_config["rss"]
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
            doi = ""
            m = re.search(r'10\.\d{4,}/[^\s"<>]+', link)
            if m:
                doi = m.group(0)

        pub_date = parse_date(entry)
        date_str = pub_date.strftime("%Y-%m-%d") if pub_date else ""

        summary = normalize_text(entry.get("summary", entry.get("description", "")))
        summary = re.sub(r'<[^>]+>', '', summary)

        # Score relevance
        score, matched = score_paper(title, summary)

        # OA detection for Elsevier RSS: check for open access indicators
        is_oa = False
        if hasattr(entry, "prism_aggregationType"):
            # RSS doesn't reliably give OA info; infer from common patterns
            pass
        # Try to infer from link or DOI landing page (best effort)

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
            "is_oa": is_oa,
            "oa_url": link if is_oa else "",
            "score": round(score, 2),
            "matched_keywords": matched,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        paper["id"] = generate_id(paper)
        papers.append(paper)

    print(f"    ✓ Got {len(papers)} papers from {journal_config['short']}")
    return papers


def fetch_crossref(journal_key: str, journal_config: dict) -> list[dict]:
    papers = []
    issn = journal_config["issn"]
    print(f"  Fetching {journal_config['short']} from CrossRef (ISSN: {issn}) ...")

    url = f"https://api.crossref.org/works?filter=issn:{issn}&sort=published&order=desc&rows=50"
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

        doi = item.get("DOI", "")
        url_link = item.get("URL", f"https://doi.org/{doi}" if doi else "")
        abstract = clean_jats_abstract(item.get("abstract", ""))

        # OA detection
        is_oa = False
        oa_url = ""
        for lic in item.get("license", []):
            start = lic.get("start", {}).get("date-time", "")
            # If license start date is before or equal to publication, treat as OA
            if start:
                is_oa = True
        # CrossRef has open-access flag in some records
        if item.get("is-referenced-by-count") is not None:
            # Not a direct OA flag; check for free full-text links
            pass
        # Best-effort: many AIP journals are hybrid, so we can't reliably detect from CrossRef alone
        # Mark as potentially OA if URL contains known OA patterns
        if "creativecommons" in str(item.get("license", [])).lower():
            is_oa = True

        # Score relevance
        score, matched = score_paper(title, abstract)

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
            "score": round(score, 2),
            "matched_keywords": matched,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
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

        # arXiv is always OA
        is_oa = True
        oa_url = link

        # Score relevance
        score, matched = score_paper(title, summary)

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
            "score": round(score, 2),
            "matched_keywords": matched,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
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

    # Filter by relevance score
    relevant = [p for p in merged if p.get("score", 0) >= SCORE_THRESHOLD]
    irrelevant = [p for p in merged if p.get("score", 0) < SCORE_THRESHOLD]

    print(f"  Relevant papers (score >= {SCORE_THRESHOLD}): {len(relevant)}")
    print(f"  Filtered out (score < {SCORE_THRESHOLD}): {len(irrelevant)}")

    # Sort by date desc, then score desc
    relevant.sort(key=lambda x: (x.get("published", ""), x.get("score", 0)), reverse=True)

    # Update data
    data["papers"] = relevant
    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    # Save
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[{datetime.now().isoformat()}] Done. Total relevant papers: {len(relevant)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
