from __future__ import annotations

import csv
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ngo_identity import ensure_ngo_id, get_ngo_id


MODULE_VERSION = "karnataka_recovery_v4_final_ownership_proof"

MODE_SPECS: dict[str, dict[str, Any]] = {
    "regression_test": {
        "label": "Optional technical audit",
        "description": "Optional historical 44-NGO audit. Production is protected by the built-in ownership self-test and is not gated on this file.",
        "max_queries_per_row": 4,
        "requires_serper": True,
        "default_concurrency": 4,
    },
    "known_url_identity": {
        "label": "1. Verify known URLs",
        "description": "Start here. Every retained URL is revalidated under current ownership rules without spending a Serper query.",
        "max_queries_per_row": 0,
        "requires_serper": False,
        "default_concurrency": 12,
    },
    "saved_candidate_fetch": {
        "label": "Verify saved candidates",
        "description": "Zero-query fetch and identity verification for historical candidates and timeout rows.",
        "max_queries_per_row": 0,
        "requires_serper": False,
        "default_concurrency": 12,
    },
    "missing_query_only": {
        "label": "2. Run missing query only",
        "description": "Runs exactly one missing logical search; transient provider retries do not spend another logical query.",
        "max_queries_per_row": 1,
        "requires_serper": True,
        "default_concurrency": 12,
    },
    "enhanced_search": {
        "label": "3. Enhanced historical recovery",
        "description": "Uses legal name, public brand, acronym, address/pincode and project-parent recovery in stages.",
        "max_queries_per_row": 3,
        "requires_serper": True,
        "default_concurrency": 12,
    },
    "new_unlinked": {
        "label": "4. New / unlinked Darpan records",
        "description": "Full staged discovery for source records with no defensible historical coverage.",
        "max_queries_per_row": 4,
        "requires_serper": True,
        "default_concurrency": 12,
    },
    "identity_collision": {
        "label": "5. Same-name identity collisions",
        "description": "Processes every source record separately and uses registration/address evidence to distinguish entities.",
        "max_queries_per_row": 3,
        "requires_serper": True,
        "default_concurrency": 6,
    },
    "firecrawl_retry": {
        "label": "6. Firecrawl fetch retry",
        "description": "No Serper. Direct-fetches first and spends Firecrawl credits only on blocked/SSL/JavaScript failures.",
        "max_queries_per_row": 0,
        "requires_serper": False,
        "default_concurrency": 4,
        "requires_firecrawl": True,
    },
}

RESULT_FILES = {
    "results": "karnataka_recovery_results.csv",
    "audit": "karnataka_recovery_audit.csv",
    "summary": "karnataka_recovery_summary.json",
    "status": "karnataka_recovery_status.json",
    "query_plan": "karnataka_recovery_query_plan.csv",
    "manual_review": "karnataka_recovery_manual_review.csv",
    "no_site": "karnataka_recovery_no_site.csv",
    "retry": "karnataka_recovery_retry_input.csv",
    "avika_input": "dfp2_recovered_websites_for_avika_filter.csv",
    "repository": "dfp2_repository_output.csv",
    "avika_audit": "dfp2_run_audit.csv",
    "avika_rejected": "dfp2_rejected_audit.csv",
    "errors": "karnataka_recovery_errors.log",
    "input": "uploaded_input.csv",
    "settings": "karnataka_recovery_settings.json",
}

RESULT_FIELDS = [
    "NGO ID", "Source Record ID", "Source Fingerprint", "Source Row Number", "Recovery Mode", "Queue Action",
    "NGO Name", "State", "District", "Darpan ID", "Registration Reference", "Registered Address", "Pincode",
    "Referral Name", "Public Name", "Project Name", "Parent Organisation", "Email", "Phone", "Sector Tags",
    "Website", "Discovery Status", "Website Status", "Page Type", "Ownership Class", "Confidence",
    "Identity Evidence", "Identity Conflicts", "Evidence Page URL", "Fetch Status", "Fetch Errors",
    "Search Provider", "Winning Query", "Query Pass", "Searched", "Logical Queries Used", "Provider Attempts",
    "Successful Searches", "Failed Searches", "Candidate Count", "Candidates Verified", "Carrier Pages Seen",
    "Firecrawl Credits Used", "Firecrawl Action", "DFP Fit Status", "Previous Website Status", "Expected Outcome",
    "Regression Expected Domain", "Regression Forbidden Domains", "Regression Check", "Regression Failure Reason",
    "Retry Required", "Retry Reason", "Note", "Checked At", "Module Version",
]

AUDIT_FIELDS = [
    "NGO ID", "Source Record ID", "NGO Name", "District", "Recovery Mode", "Event Time", "Stage", "Provider",
    "Logical Query Number", "Provider Attempt Number", "Query Pass", "Query", "Candidate Rank", "Candidate URL",
    "Candidate Title", "Candidate Snippet", "Candidate Source", "Candidate Domain", "Page Type", "Candidate Score",
    "Decision", "Reject Reason", "Fetch Status", "Fetch Error", "Evidence", "Conflict", "Firecrawl Credits Used", "Note",
]

QUERY_PLAN_FIELDS = [
    "NGO ID", "Source Record ID", "NGO Name", "District", "Recovery Mode", "Maximum Logical Queries", "Query Number",
    "Query Pass", "Query", "Uses Existing URL First", "Candidate URL Count",
]

TERMINAL_DISCOVERY_STATUSES = {
    "verified_owned_site",
    "verified_controlled_microsite",
    "verified_parent_or_project_page",
    "plausible_site_identity_review",
    "no_owned_site_after_enhanced_recovery",
    "no_candidate_in_uploaded_row",
    "candidate_fetch_pending",
    "search_partial",
    "skipped_query_cap",
    "collision_identity_review",
}

VERIFIED_STATUSES = {
    "verified_owned_site",
    "verified_controlled_microsite",
    "verified_parent_or_project_page",
}

MANUAL_STATUSES = {"plausible_site_identity_review", "collision_identity_review"}
RETRY_STATUSES = {"candidate_fetch_pending", "search_partial", "skipped_query_cap", "provider_blocked"}

LEGAL_SUFFIXES = {
    "trust", "foundation", "society", "samsthe", "samiti", "samithi", "sanstha", "sangha", "mission",
    "association", "organisation", "organization", "charitable", "welfare", "seva", "development",
    "educational", "education", "rural", "public", "registered", "regd", "ngo",
}
STOPWORDS = {"the", "of", "for", "and", "in", "a", "an", "to", "by", "at", "on"}
GENERIC_TOKENS = {
    "asha", "seva", "hope", "care", "help", "sadhana", "pragathi", "pragati", "jeevan", "jyothi", "jyoti",
    "vision", "divine", "mercy", "india", "indian", "children", "child", "school", "home", "special",
    "academy", "centre", "center", "community", "rural", "public", "foundation", "trust", "society",
    "welfare", "development", "education", "educational", "charitable", "social", "reach",
    "support", "service", "services", "initiative", "programme", "program",
}

DIRECTORY_DOMAINS = {
    "ngo4you.com", "csridentity.com", "oneindia.com", "ngosindia.com", "ngosindia.org", "ngodarpan.gov.in",
    "ngodarpan.gov", "darpan.gov.in", "zaubacorp.com", "falconebiz.com", "indiafilings.com", "justdial.com",
    "sulekha.com", "indiamart.com", "tracxn.com", "guidestarindia.org", "guidestar.org", "csrbox.org",
    "ngobox.org", "give.do", "globalgiving.org", "crunchbase.com", "rocketreach.co", "signalhire.com",
    "companydetails.in", "thecompanycheck.com", "tofler.in", "indiankanoon.org", "supremetoday.ai",
    "milaap.org", "helpyourngo.com", "myngos.in", "ivolunteer.in", "divyangsathi.com", "tatanexarc.com",
    "trip.com", "ixigo.com", "wypages.com", "aurumproptech.in", "tradeindia.com", "studyriserr.com",
    "play.google.com", "scribd.com", "slideshare.net", "academia.edu", "researchgate.net",
    "ketto.org", "impactguru.com", "giveindia.org", "fundrazr.com", "gofundme.com",
    "orellsoft.com", "indiacustomercare.com", "bharatibiz.com", "asklaila.com", "yappe.in",
    "mappls.com", "mapquest.com", "foursquare.com", "wanderlog.com", "top-rated.online",
}
SOCIAL_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
    "wa.me", "whatsapp.com", "threads.net",
}
NEWS_DOMAINS = {
    "thehindu.com", "timesofindia.indiatimes.com", "hindustantimes.com", "indianexpress.com", "deccanherald.com",
    "newindianexpress.com", "yourstory.com", "thebetterindia.com", "medium.com", "news18.com", "ndtv.com",
    "indiatoday.in", "scroll.in", "thenewsminute.com", "indiancatholicmatters.org", "ecohq.in",
}
REFERENCE_DOMAINS = {
    "ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "wpi.edu", "ieeexplore.ieee.org", "substack.com",
    "researchgate.net", "academia.edu", "semanticscholar.org", "springer.com", "sciencedirect.com",
    "mha.gov.in", "planning.karnataka.gov.in", "kla.kar.nic.in", "fcraonline.nic.in", "unodc.org",
    "ohchr.org", "fs.usda.gov", "iufro.org", "thenationaltrust.gov.in", "thenationaltrust.in",
}
FOREIGN_CONFLICT_TLDS = {"pk"}
CARRIER_PATH_MARKERS = (
    "/fundraiser", "/fundraisers", "/ngo-details/", "/company/", "/client", "/clients/",
    "/our-clients", "/travel-guide/", "/attraction/", "/document/", "/documents/", "/article/",
    "/articles/", "/blog/", "/post/", "/posts/", "/news/", "/discover/", "/case-study",
    "/case-studies", "/portfolio/", "/grantee", "/partners/", "/press/", "/media/", "/research/",
    "/publication", "/papers/", "/school-kit-distribution", "/distribution-at-",
    "/institutional-directory", "/registered_organization",
)
CARRIER_TITLE_TERMS = (
    "fundraiser", "company profile", "client we serve", "clients we serve", "our clients", "travel guide",
    "attraction", "ngos in", "ngo in karnataka", "ngo details", "registered organisations",
    "registered organizations", "list of ngos", "research article", "research paper", "country report",
    "government of india", "school kit distribution", "case study", "customer story", "partner spotlight",
    "grantee story", "supported by", "funded by", "pdf",
)
HOSTED_PLATFORMS = {
    "1ngo.in", "site123.me", "wixsite.com", "wordpress.com", "blogspot.com", "weebly.com", "sites.google.com",
    "mystrikingly.com", "webs.com", "godaddysites.com",
}
FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "rediffmail.com", "icloud.com", "protonmail.com",
}

NAME_KEYS = ("name", "ngo_name", "NGO Name", "Organisation", "Organization", "organisation", "organization")
DISTRICT_KEYS = ("district", "District")
STATE_KEYS = ("state", "State")
NGO_ID_KEYS = ("ngo_id", "NGO ID", "dfp_ngo_id", "DFP NGO ID", "unique_ngo_id", "Unique NGO ID")
SOURCE_ID_KEYS = ("source_record_id", "Source Record ID", "source_id", "Source ID")
DARPA_KEYS = ("darpan_id", "Darpan ID", "unique_id", "Unique ID", "ngo_darpan_id")
REG_KEYS = ("registration_reference", "Registration Reference", "registration_descriptor", "Registration Descriptor", "registration_no", "Registration No")
ADDRESS_KEYS = ("registered_address", "Registered Address", "address", "Address")
PINCODE_KEYS = ("pincode", "Pincode", "pin_code", "PIN Code", "Pin Code")
WEBSITE_KEYS = (
    "recheck_candidate_url", "candidate_url", "Candidate URL", "website", "Website", "initial_website",
    "Initial Website", "supplied_url", "Supplied URL", "url", "URL", "Website / Source",
)
REFERRAL_KEYS = ("referral_name", "Referral Name", "recommended_name", "Recommendation Name")
PUBLIC_KEYS = ("public_name", "Public Name", "public_brand", "Public Brand", "brand", "Brand")
PROJECT_KEYS = ("project_name", "Project Name", "programme_name", "Programme Name", "program_name", "Program Name", "school_name", "School Name")
PARENT_KEYS = ("parent_organisation", "Parent Organisation", "parent_organization", "Parent Organization", "operator", "Operator")
EMAIL_KEYS = ("email", "Email", "email_id", "Email ID")
PHONE_KEYS = ("phone", "Phone", "contact_number", "Contact Number", "mobile", "Mobile")
SECTOR_KEYS = ("sector_tags", "Sector Tags", "sectors", "Sectors")
ACTION_KEYS = ("next_action", "Next Action", "queue_action", "Queue Action")
FAILED_QUERY_KEYS = ("failed_query_passes", "Failed Query Passes")
MODE_OVERRIDE_KEYS = ("recovery_mode_override", "Recovery Mode Override", "recovery_mode", "Recovery Mode")
PREVIOUS_STATUS_KEYS = ("previous_website_status", "Previous Website Status", "initial_status_or_reason", "Initial Status or Reason")
EXPECTED_OUTCOME_KEYS = ("expected_outcome", "Expected Outcome", "regression_expected_outcome", "Regression Expected Outcome")
EXPECTED_DOMAIN_KEYS = ("regression_expected_domain", "Regression Expected Domain", "expected_domain", "Expected Domain")
FORBIDDEN_DOMAIN_KEYS = ("regression_forbidden_domains", "Regression Forbidden Domains", "forbidden_domains", "Forbidden Domains")
DFP_FIT_KEYS = ("dfp_fit_status", "DFP Fit Status", "programme_fit_status", "Program Fit Status", "shortlist_status", "Shortlist Status")


class ProviderUnavailable(RuntimeError):
    def __init__(self, provider: str, reason: str, detail: str = ""):
        super().__init__(f"{provider}: {reason}{(': ' + detail) if detail else ''}")
        self.provider = provider
        self.reason = reason
        self.detail = detail


class QueryCapReached(RuntimeError):
    pass


class RowDeadlineReached(TimeoutError):
    def __init__(self, ctx: "RowContext"):
        super().__init__("row deadline reached")
        self.ctx = ctx


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(value: Any) -> str:
    value = html.unescape(str(value or "")).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", norm(value))


def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def first_value(row: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    lower_map = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        value = lower_map.get(str(key).strip().lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def extract_pincode(value: Any) -> str:
    explicit = re.search(r"(?<!\d)([1-9]\d{5})(?!\d)", str(value or ""))
    return explicit.group(1) if explicit else ""


def tokenise_name(value: Any, *, drop_legal: bool = True) -> list[str]:
    tokens = [t for t in norm(value).split() if t not in STOPWORDS]
    if drop_legal:
        tokens = [t for t in tokens if t not in LEGAL_SUFFIXES]
    return tokens


def distinctive_tokens(value: Any) -> list[str]:
    tokens = tokenise_name(value)
    return [t for t in tokens if len(t) >= 3 and t not in GENERIC_TOKENS]


def acronym(value: Any) -> str:
    words = [w for w in tokenise_name(value) if len(w) > 1]
    if len(words) < 3:
        return ""
    candidate = "".join(w[0] for w in words).upper()
    return candidate if 3 <= len(candidate) <= 12 else ""


def raw_acronym(value: Any) -> str:
    """Initials including legal-form words when those initials form the public domain.

    Examples: VIKAS DISABLED CHARITABLE TRUST -> VDCT and
    Shree Ramana Maharishi Academy for the Blind -> SRMAB.
    """
    words = [w for w in norm(value).split() if w not in STOPWORDS and len(w) > 1]
    if len(words) < 3:
        return ""
    candidate = "".join(w[0] for w in words).upper()
    return candidate if 3 <= len(candidate) <= 12 else ""


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def sha20(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:20]


def mask_key(key: str) -> str:
    return "..." + key[-6:] if len(key) > 6 else "..."


def hostname(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def domain_matches(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_public_hostname(host: str) -> bool:
    if not host or "." not in host or host.endswith("."):
        return False
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return False
    # Do not perform DNS during candidate nomination. DNS lookups can stall a
    # high-concurrency run and are repeated again by the actual HTTP client.
    # Literal private/reserved IPs are rejected synchronously; normal hostnames
    # are validated syntactically and classified by the fetch layer.
    try:
        parsed_ip = ipaddress.ip_address(host)
        return not (parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_reserved or parsed_ip.is_multicast)
    except ValueError:
        pass
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", host))


def normalise_url(value: Any) -> str:
    raw = str(value or "").strip().strip("'\"")
    if not raw:
        return ""
    raw = raw.replace("\\", "/")
    if raw.startswith("//"):
        raw = "https:" + raw
    if not re.match(r"^https?://", raw, flags=re.I):
        raw = "https://" + raw.lstrip("/")
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if not is_public_hostname(host):
            return ""
        netloc = host
        if parsed.port and parsed.port not in {80, 443}:
            netloc += f":{parsed.port}"
        path = parsed.path or "/"
        return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))
    except Exception:
        return ""


def url_variants(value: Any, limit: int = 2) -> list[str]:
    base = normalise_url(value)
    if not base:
        return []
    parsed = urlparse(base)
    host = parsed.hostname or ""
    candidates = [base]
    toggled_host = host[4:] if host.startswith("www.") else "www." + host
    candidates.append(urlunparse((parsed.scheme, toggled_host, parsed.path or "/", "", parsed.query, "")))
    candidates.append(urlunparse(("http" if parsed.scheme == "https" else "https", host, parsed.path or "/", "", parsed.query, "")))
    out: list[str] = []
    for candidate in candidates:
        candidate = normalise_url(candidate)
        if candidate and candidate not in out:
            out.append(candidate)
        if len(out) >= max(1, limit):
            break
    return out


def extract_urls(value: Any) -> list[str]:
    text = str(value or "")
    raw_urls = re.findall(r"(?:https?://|www\.)[^\s,;|\]\[()<>]+", text, flags=re.I)
    if not raw_urls and text.strip() and ("." in text) and " " not in text.strip():
        raw_urls = [text.strip()]
    out: list[str] = []
    for raw in raw_urls:
        url = normalise_url(raw.rstrip(".,;:!?)]}"))
        if url and url not in out:
            out.append(url)
    return out


def previous_website_status(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    for key in (
        "previous_website_status", "Previous Website Status", "initial_status_or_reason",
        "Initial Status or Reason", "website_status", "Website Status",
    ):
        value = norm(row.get(key))
        if value:
            return value
    return ""


def is_historical_mismatch(row: dict[str, Any] | None) -> bool:
    status = previous_website_status(row)
    return "mismatch" in status or "wrong website" in status or "wrong site" in status


def domain_identity_evidence(row: dict[str, Any] | None, url: str) -> dict[str, Any]:
    """Return a conservative domain-to-entity match.

    Strength 3/4 is suitable ownership evidence, strength 2 is supporting
    evidence, and strength 1 is only a clue. Partial matches such as
    ``donboscosouthasia.org`` for ``Don Bosco BREADS`` are deliberately weak.
    """
    if not row:
        return {"strength": 0, "signals": []}
    host = hostname(url)
    host_compact = compact(host)
    host_parts = {part for part in norm(host.replace(".", " ")).split() if part}
    hosted = domain_matches(host, HOSTED_PLATFORMS)
    hosted_prefix = host
    for platform in HOSTED_PLATFORMS:
        if host == platform:
            hosted_prefix = ""
            break
        if host.endswith("." + platform):
            hosted_prefix = host[: -(len(platform) + 1)]
            break
    prefix_compact = compact(hosted_prefix)
    best = 0
    signals: list[str] = []

    for alias in identity_aliases(row):
        alias_norm = norm(alias)
        tokens = distinctive_tokens(alias)
        compact_alias = "".join(tokens)
        domain_form_tokens = [
            token for token in alias_norm.split()
            if token not in STOPWORDS and token not in {
                "charitable", "welfare", "development", "educational", "education", "rural",
                "social", "public", "registered", "regd", "ngo", "organisation", "organization",
            }
        ]
        domain_form = "".join(domain_form_tokens)
        domain_form_has_weight = any(token not in GENERIC_TOKENS and token not in LEGAL_SUFFIXES for token in domain_form_tokens) or any(token in LEGAL_SUFFIXES for token in domain_form_tokens)
        if domain_form_has_weight and len(domain_form_tokens) >= 2 and len(domain_form) >= 7 and domain_form in host_compact:
            best = max(best, 3)
            signals.append(f"domain contains legal/public identity form: {domain_form}")
        if len(compact_alias) >= 6 and compact_alias in host_compact:
            best = max(best, 3)
            signals.append(f"domain contains full identity phrase: {compact_alias}")
        for acro in (acronym(alias), raw_acronym(alias)):
            acro_compact = compact(acro)
            if acro_compact and len(acro_compact) >= 3 and (acro_compact in host_parts or acro_compact in host_compact):
                best = max(best, 3)
                signals.append(f"domain contains organisation acronym: {acro}")
        hits = [token for token in tokens if len(token) >= 4 and (token in host_parts or token in host_compact)]
        if len(tokens) == 1 and hits and len(tokens[0]) >= 6:
            best = max(best, 2)
            signals.append(f"domain contains distinctive identity token: {tokens[0]}")
        elif len(tokens) == 2 and len(hits) == 2:
            best = max(best, 3)
            signals.append("domain contains both identity tokens: " + ", ".join(hits))
        elif len(tokens) >= 3 and len(hits) == len(tokens):
            best = max(best, 3)
            signals.append("domain contains all identity tokens: " + ", ".join(hits[:5]))
        elif len(tokens) >= 4 and len(hits) >= len(tokens) - 1:
            best = max(best, 2)
            signals.append("domain contains most identity tokens: " + ", ".join(hits[:5]))
        elif len(hits) >= 2:
            best = max(best, 1)
            signals.append("domain contains partial identity tokens: " + ", ".join(hits[:4]))

        # Hosted microsites often expose the public brand only in the subdomain.
        # Exact subdomain-to-alias matching is supporting evidence even for a
        # generic one-word public name such as Sadhana.
        alias_first = compact(alias_norm.split()[0]) if alias_norm else ""
        if hosted and prefix_compact and alias_first and len(alias_first) >= 5 and (prefix_compact == alias_first or prefix_compact.startswith(alias_first)):
            best = max(best, 2)
            signals.append(f"hosted subdomain matches public identity: {alias_first}")

    email = str(row.get("email") or "").strip().lower()
    if "@" in email:
        email_host = email.rsplit("@", 1)[-1].lstrip("www.")
        if email_host and (host == email_host or host.endswith("." + email_host)):
            best = max(best, 4)
            signals.append("domain matches organisational email")
    return {"strength": best, "signals": list(dict.fromkeys(signals))}


def domain_identity_signals(row: dict[str, Any] | None, url: str) -> list[str]:
    return list(domain_identity_evidence(row, url).get("signals") or [])


def obvious_carrier_reason(url: str, title: str = "", snippet: str = "", row: dict[str, Any] | None = None) -> str:
    host = hostname(url)
    parsed = urlparse(url)
    path = (parsed.path or "/").lower()
    blob = norm(" ".join([title, snippet]))
    domain_signals = domain_identity_signals(row, url)
    if domain_matches(host, DIRECTORY_DOMAINS):
        return "known directory, fundraising, marketplace or profile platform"
    if domain_matches(host, SOCIAL_DOMAINS):
        return "social-media page"
    if domain_matches(host, NEWS_DOMAINS):
        return "known news or profile publisher"
    if domain_matches(host, REFERENCE_DOMAINS) or host.endswith(".gov.in") or host.endswith(".nic.in") or host.endswith(".edu") or host.endswith(".ac.in"):
        return "government, academic or reference source"
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    if tld in FOREIGN_CONFLICT_TLDS:
        return f"foreign country-code domain .{tld} conflicts with Karnataka identity"
    if path.endswith(".pdf") or "/pdf/" in path or "/pdf-" in path:
        return "PDF/reference document"
    if not domain_signals:
        if any(marker in path for marker in CARRIER_PATH_MARKERS):
            return "article, directory, fundraiser, client or profile URL pattern"
        if re.search(r"/(?:19|20)\d{2}/\d{1,2}/(?:\d{1,2}/)?", path):
            return "dated article/blog URL pattern"
        if any(term in blob for term in CARRIER_TITLE_TERMS):
            return "search title indicates a directory, article, client list or reference page"
    return ""


def page_type_for_candidate(url: str, title: str = "", snippet: str = "", row: dict[str, Any] | None = None) -> str:
    host = hostname(url)
    carrier_reason = obvious_carrier_reason(url, title, snippet, row)
    if carrier_reason:
        if domain_matches(host, SOCIAL_DOMAINS):
            return "social_media"
        if domain_matches(host, DIRECTORY_DOMAINS):
            return "directory_or_registry"
        if domain_matches(host, NEWS_DOMAINS):
            return "article_or_profile"
        if "foreign country-code" in carrier_reason:
            return "wrong_entity"
        if "government" in carrier_reason or "academic" in carrier_reason or "reference" in carrier_reason or "PDF" in carrier_reason:
            return "government_academic_or_document_reference"
        return "third_party_mention_candidate"
    if domain_matches(host, HOSTED_PLATFORMS):
        return "controlled_hosted_microsite_candidate"
    blob = norm(" ".join([title, snippet, url]))
    # These words are useful only when the host does not itself carry the NGO's
    # identity.  On an NGO-owned domain, a news/story/donate page is still owned.
    if not domain_identity_signals(row, url) and re.search(
        r"\b(donate[sd]? to|supported by|partner(?:ed)? with|grantee|funded by|client(?:s)?|case study|news|article|story|profile)\b",
        blob,
    ):
        return "third_party_mention_candidate"
    if row:
        project = norm(row.get("project_name"))
        parent = norm(row.get("parent_organisation"))
        if (project and project in blob) or (parent and parent in blob):
            return "parent_or_project_candidate"
    return "owned_site_candidate"


def page_type_after_verification(candidate_type: str, url: str, row: dict[str, Any], text: str) -> str:
    host = hostname(url)
    if domain_matches(host, HOSTED_PLATFORMS):
        return "controlled_hosted_microsite"
    relation_blob = norm(text[:30000])
    project = norm(row.get("project_name") or row.get("referral_name"))
    parent = norm(row.get("parent_organisation"))
    legal = norm(row.get("name"))
    relation_terms = any(term in relation_blob for term in [
        "project of", "initiative of", "operated by", "run by", "managed by", "unit of",
        "a programme of", "a program of", "part of", "under the aegis of",
    ])
    if candidate_type == "parent_or_project_candidate" and relation_terms:
        return "verified_parent_or_project_page"
    if project and project in relation_blob and parent and parent in relation_blob and relation_terms:
        return "verified_parent_or_project_page"
    if parent and parent in relation_blob and legal and legal in relation_blob and relation_terms:
        return "verified_parent_or_project_page"
    return "owned_organisation_site"


@dataclass
class AuditEvent:
    stage: str
    provider: str = ""
    logical_query_number: int = 0
    provider_attempt_number: int = 0
    query_pass: str = ""
    query: str = ""
    candidate_rank: int = 0
    candidate_url: str = ""
    candidate_title: str = ""
    candidate_snippet: str = ""
    candidate_source: str = ""
    page_type: str = ""
    candidate_score: Any = ""
    decision: str = ""
    reject_reason: str = ""
    fetch_status: str = ""
    fetch_error: str = ""
    evidence: str = ""
    conflict: str = ""
    firecrawl_credits_used: int = 0
    note: str = ""


@dataclass
class RowContext:
    row: dict[str, Any]
    mode: str
    deadline_at: float
    max_queries: int
    logical_queries_used: int = 0
    provider_attempts: int = 0
    successful_searches: int = 0
    failed_searches: int = 0
    candidate_count: int = 0
    candidates_verified: int = 0
    carrier_pages_seen: int = 0
    firecrawl_credits_used: int = 0
    best_candidate: dict[str, Any] | None = None
    best_verification: dict[str, Any] | None = None
    audit: list[AuditEvent] = field(default_factory=list)

    def remaining(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())

    def check_deadline(self) -> None:
        if self.remaining() <= 0:
            raise RowDeadlineReached(self)


@dataclass
class KeyState:
    key: str
    state: str = "unknown"
    cooldown_until: float = 0.0
    requests: int = 0
    successes: int = 0
    failures: int = 0
    last_status: int | None = None
    last_error: str = ""
    remaining_credits: int | None = None
    semaphore: threading.BoundedSemaphore | None = None


class SerperPool:
    def __init__(self, keys: list[str], per_key_concurrency: int, preflight_cache: dict[str, dict[str, Any]]):
        self.states = [KeyState(key=k, semaphore=threading.BoundedSemaphore(max(1, per_key_concurrency))) for k in keys]
        self.lock = threading.RLock()
        self.index = 0
        self.preflight_cache = preflight_cache
        self.preflight_queries = 0
        self.provider_attempts = 0

    @staticmethod
    def permanent_error(status: int, body: str) -> bool:
        low = str(body or "").lower()
        markers = (
            "not enough credits", "not enough credit", "insufficient credit", "insufficient credits", "credits exhausted",
            "credit balance", "payment required", "billing", "invalid api key", "unauthorized", "forbidden", "quota exceeded",
        )
        return status in {401, 402, 403} or any(marker in low for marker in markers)

    def preflight(self, enabled: bool = True) -> list[dict[str, Any]]:
        if not enabled:
            for state in self.states:
                state.state = "healthy_unchecked"
            return self.stats()
        for state in self.states:
            cache_key = sha20(state.key)
            cached = self.preflight_cache.get(cache_key) or {}
            if time.time() - float(cached.get("checked_at", 0)) < 1800 and cached.get("state") in {"healthy", "exhausted", "invalid"}:
                state.state = str(cached.get("state"))
                state.last_status = cached.get("last_status")
                state.last_error = str(cached.get("last_error") or "")
                continue
            try:
                self.preflight_queries += 1
                response = requests.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": state.key, "Content-Type": "application/json"},
                    json={"q": "DFP Karnataka recovery provider preflight", "num": 1, "gl": "in", "hl": "en"},
                    timeout=15,
                )
                state.last_status = response.status_code
                if response.status_code == 200:
                    state.state = "healthy"
                    state.successes += 1
                elif self.permanent_error(response.status_code, response.text[:500]):
                    state.state = "exhausted" if "credit" in response.text.lower() or response.status_code == 402 else "invalid"
                    state.last_error = response.text[:250]
                elif response.status_code == 429:
                    state.state = "cooling_down"
                    state.cooldown_until = time.monotonic() + 30
                    state.last_error = response.text[:250]
                else:
                    state.state = "temporarily_unavailable"
                    state.last_error = response.text[:250]
            except requests.RequestException as exc:
                state.state = "temporarily_unavailable"
                state.last_error = str(exc)[:250]
            self.preflight_cache[cache_key] = {
                "checked_at": time.time(), "state": state.state, "last_status": state.last_status, "last_error": state.last_error,
            }
        return self.stats()

    def healthy_count(self) -> int:
        now = time.monotonic()
        return sum(1 for s in self.states if s.state not in {"exhausted", "invalid", "disabled"} and s.cooldown_until <= now)

    def _lease(self, timeout: float) -> KeyState:
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            now = time.monotonic()
            with self.lock:
                states = self.states
                if not states:
                    raise ProviderUnavailable("serper", "account_not_configured")
                usable = [s for s in states if s.state not in {"exhausted", "invalid", "disabled"}]
                if not usable:
                    detail = " | ".join(f"{mask_key(s.key)}:{s.state}" for s in states)
                    raise ProviderUnavailable("serper", "account_exhausted_or_invalid", detail)
                for offset in range(len(states)):
                    idx = (self.index + offset) % len(states)
                    state = states[idx]
                    if state.state in {"exhausted", "invalid", "disabled"} or state.cooldown_until > now:
                        continue
                    if state.semaphore and state.semaphore.acquire(blocking=False):
                        self.index = (idx + 1) % len(states)
                        return state
            time.sleep(0.05)
        raise ProviderUnavailable("serper", "account_busy_or_cooling_down")

    def search(self, query: str, timeout: int = 20) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        max_attempts = max(2, len(self.states) * 2)
        for attempt_no in range(1, max_attempts + 1):
            state = self._lease(timeout=max(timeout, 10))
            try:
                with self.lock:
                    state.requests += 1
                    self.provider_attempts += 1
                response = requests.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": state.key, "Content-Type": "application/json"},
                    json={"q": query, "num": 10, "gl": "in", "hl": "en"},
                    timeout=timeout,
                )
                body = response.text[:500]
                with self.lock:
                    state.last_status = response.status_code
                attempts.append({"attempt": attempt_no, "key": mask_key(state.key), "status": response.status_code, "body": body})
                if response.status_code == 200:
                    with self.lock:
                        state.state = "healthy"
                        state.successes += 1
                    return response.json(), attempts
                with self.lock:
                    state.failures += 1
                    state.last_error = body[:250]
                if response.status_code == 429:
                    retry_after = safe_int(response.headers.get("Retry-After"), 20)
                    with self.lock:
                        state.state = "cooling_down"
                        state.cooldown_until = time.monotonic() + max(2, retry_after)
                    continue
                if self.permanent_error(response.status_code, body):
                    with self.lock:
                        state.state = "exhausted" if ("credit" in body.lower() or response.status_code == 402) else "invalid"
                    continue
                if response.status_code >= 500:
                    with self.lock:
                        state.state = "temporarily_unavailable"
                        state.cooldown_until = time.monotonic() + 2
                    continue
                raise RuntimeError(f"Serper HTTP {response.status_code}: {body}")
            except requests.RequestException as exc:
                with self.lock:
                    state.failures += 1
                    state.last_error = str(exc)[:250]
                    state.state = "temporarily_unavailable"
                    state.cooldown_until = time.monotonic() + 1
                attempts.append({"attempt": attempt_no, "key": mask_key(state.key), "status": "request_error", "body": str(exc)[:250]})
                continue
            finally:
                if state.semaphore:
                    state.semaphore.release()
        detail = " | ".join(f"{a['key']}:{a['status']}" for a in attempts[-6:])
        if self.healthy_count() <= 0:
            raise ProviderUnavailable("serper", "account_exhausted_or_unavailable", detail)
        raise RuntimeError("Serper logical query failed after key failover: " + detail)

    def stats(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self.lock:
            return [
                {
                    "key": mask_key(s.key), "state": s.state, "requests": s.requests, "successes": s.successes,
                    "failures": s.failures, "last_status": s.last_status, "last_error": s.last_error,
                    "cooldown_seconds": round(max(0.0, s.cooldown_until - now), 1),
                }
                for s in self.states
            ]


class FirecrawlPool:
    def __init__(self, keys: list[str], budget: int, preflight_cache: dict[str, dict[str, Any]], proxy: str = "basic"):
        self.states = [KeyState(key=k, semaphore=threading.BoundedSemaphore(2)) for k in keys]
        self.budget = max(0, int(budget))
        self.used = 0
        self.lock = threading.RLock()
        self.index = 0
        self.preflight_cache = preflight_cache
        self.proxy = proxy if proxy in {"basic", "auto", "enhanced"} else "basic"

    def preflight(self) -> list[dict[str, Any]]:
        for state in self.states:
            cache_key = sha20(state.key)
            cached = self.preflight_cache.get(cache_key) or {}
            if time.time() - float(cached.get("checked_at", 0)) < 900 and cached.get("state") in {"healthy", "exhausted", "invalid"}:
                state.state = str(cached.get("state"))
                state.remaining_credits = cached.get("remaining_credits")
                continue
            try:
                response = requests.get(
                    "https://api.firecrawl.dev/v2/team/credit-usage",
                    headers={"Authorization": f"Bearer {state.key}"}, timeout=15,
                )
                state.last_status = response.status_code
                body = response.text[:500]
                if response.status_code == 200:
                    payload = response.json()
                    remaining = safe_int(((payload.get("data") or {}).get("remainingCredits")), -1)
                    state.remaining_credits = remaining if remaining >= 0 else None
                    state.state = "healthy" if remaining != 0 else "exhausted"
                elif response.status_code == 402:
                    state.state = "exhausted"
                    state.last_error = body[:250]
                elif response.status_code in {401, 403}:
                    state.state = "invalid"
                    state.last_error = body[:250]
                else:
                    state.state = "temporarily_unavailable"
                    state.last_error = body[:250]
            except requests.RequestException as exc:
                state.state = "temporarily_unavailable"
                state.last_error = str(exc)[:250]
            self.preflight_cache[cache_key] = {
                "checked_at": time.time(), "state": state.state, "remaining_credits": state.remaining_credits,
            }
        return self.stats()

    def _reserve(self, estimated: int = 1) -> None:
        with self.lock:
            if self.used + estimated > self.budget:
                raise ProviderUnavailable("firecrawl", "run_credit_budget_reached", f"used={self.used}, budget={self.budget}")
            self.used += estimated

    def _lease(self) -> KeyState:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with self.lock:
                healthy = [s for s in self.states if s.state not in {"exhausted", "invalid", "disabled"}]
                if not healthy:
                    raise ProviderUnavailable("firecrawl", "no_healthy_key")
                for offset in range(len(self.states)):
                    idx = (self.index + offset) % len(self.states)
                    state = self.states[idx]
                    if state.state in {"exhausted", "invalid", "disabled"}:
                        continue
                    if state.semaphore and state.semaphore.acquire(blocking=False):
                        self.index = (idx + 1) % len(self.states)
                        return state
            time.sleep(0.1)
        raise ProviderUnavailable("firecrawl", "keys_busy")

    def scrape(self, url: str, timeout_sec: int = 45) -> dict[str, Any]:
        self._reserve(1)
        state = self._lease()
        try:
            state.requests += 1
            response = requests.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {state.key}", "Content-Type": "application/json"},
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": False,
                    "proxy": self.proxy,
                    "timeout": min(60000, max(1000, int(timeout_sec * 1000))),
                    "skipTlsVerification": True,
                    "blockAds": True,
                    "removeBase64Images": True,
                    "parsers": [],
                },
                timeout=max(10, timeout_sec + 10),
            )
            state.last_status = response.status_code
            body = response.text[:500]
            if response.status_code == 200:
                payload = response.json()
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                markdown = str((data or {}).get("markdown") or (data or {}).get("content") or "")
                metadata = (data or {}).get("metadata") or {}
                credits = safe_int(payload.get("creditsUsed") or (data or {}).get("creditsUsed"), 1)
                credits = max(1, credits)
                if credits > 1:
                    with self.lock:
                        self.used += credits - 1
                state.successes += 1
                return {"ok": True, "text": markdown, "url": str(metadata.get("sourceURL") or metadata.get("url") or url), "credits": credits, "status": 200}
            state.failures += 1
            state.last_error = body[:250]
            if response.status_code == 402:
                state.state = "exhausted"
            elif response.status_code in {401, 403}:
                state.state = "invalid"
            return {"ok": False, "text": "", "url": url, "credits": 1, "status": response.status_code, "error": body}
        except requests.RequestException as exc:
            state.failures += 1
            state.last_error = str(exc)[:250]
            return {"ok": False, "text": "", "url": url, "credits": 1, "status": "request_error", "error": str(exc)[:250]}
        finally:
            if state.semaphore:
                state.semaphore.release()

    def stats(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "key": mask_key(s.key), "state": s.state, "requests": s.requests, "successes": s.successes,
                    "failures": s.failures, "last_status": s.last_status, "remaining_credits": s.remaining_credits,
                    "last_error": s.last_error,
                }
                for s in self.states
            ]


def parse_env_keys(multi_name: str, single_name: str) -> list[str]:
    """Parse a provider key list.

    Firecrawl may still use a small failover pool. Serper intentionally does not:
    the Karnataka module uses exactly one SERPER_API_KEY/account so concurrency,
    credit planning and pause behaviour are transparent.
    """
    if single_name == "SERPER_API_KEY":
        key = str(os.environ.get("SERPER_API_KEY", "") or "").strip()
        return [key] if key else []
    raw = (os.environ.get(multi_name, "") or "") + "\n" + (os.environ.get(single_name, "") or "")
    out: list[str] = []
    for key in re.split(r"[,\s]+", raw):
        key = key.strip()
        if key and key not in out:
            out.append(key)
    return out


def serper_account_warning() -> str:
    legacy = str(os.environ.get("SERPER_API_KEYS", "") or "").strip()
    return "SERPER_API_KEYS is ignored; configure only SERPER_API_KEY." if legacy else ""


def canonicalise_row(raw: dict[str, Any], row_number: int, mode: str, used_ids: dict[str, int]) -> dict[str, Any]:
    name = first_value(raw, NAME_KEYS)
    district = first_value(raw, DISTRICT_KEYS)
    state = first_value(raw, STATE_KEYS) or "Karnataka"
    registration = first_value(raw, REG_KEYS)
    address = first_value(raw, ADDRESS_KEYS)
    pincode = first_value(raw, PINCODE_KEYS) or extract_pincode(address)
    supplied_id = first_value(raw, SOURCE_ID_KEYS)
    fingerprint_seed = "|".join([name, district, state, registration, address, str(row_number)])
    fingerprint = first_value(raw, ("source_fingerprint", "Source Fingerprint")) or sha20(fingerprint_seed)
    base_id = supplied_id or f"KA-RECOVERY-{row_number:05d}-{fingerprint[:8]}"
    used_ids[base_id] = used_ids.get(base_id, 0) + 1
    source_id = base_id if used_ids[base_id] == 1 else f"{base_id}-ROW{used_ids[base_id]}"
    override = first_value(raw, MODE_OVERRIDE_KEYS).strip().lower()
    effective_mode = override if override in MODE_SPECS else mode
    row = {str(k).strip(): ("" if v is None else str(v).strip()) for k, v in raw.items() if k is not None}
    row.update({
        "name": name,
        "district": district,
        "state": state,
        "source_record_id": source_id,
        "source_fingerprint": fingerprint,
        "source_row_number": row_number,
        "darpan_id": first_value(raw, DARPA_KEYS),
        "registration_reference": registration,
        "registered_address": address,
        "pincode": pincode,
        "referral_name": first_value(raw, REFERRAL_KEYS),
        "public_name": first_value(raw, PUBLIC_KEYS),
        "project_name": first_value(raw, PROJECT_KEYS),
        "parent_organisation": first_value(raw, PARENT_KEYS),
        "email": first_value(raw, EMAIL_KEYS),
        "phone": first_value(raw, PHONE_KEYS),
        "sector_tags": first_value(raw, SECTOR_KEYS),
        "queue_action": first_value(raw, ACTION_KEYS),
        "failed_query_passes": first_value(raw, FAILED_QUERY_KEYS),
        "previous_website_status": first_value(raw, PREVIOUS_STATUS_KEYS),
        "expected_outcome": first_value(raw, EXPECTED_OUTCOME_KEYS),
        "regression_expected_domain": first_value(raw, EXPECTED_DOMAIN_KEYS),
        "regression_forbidden_domains": first_value(raw, FORBIDDEN_DOMAIN_KEYS),
        "dfp_fit_status": first_value(raw, DFP_FIT_KEYS),
        "recovery_mode": effective_mode,
    })
    # Preserve a historical ID when supplied; otherwise generate a stable DFP ID
    # from the strongest available legal/domain/source identity.
    row["ngo_id"] = get_ngo_id({**row, "NGO ID": first_value(raw, NGO_ID_KEYS)}, context=f"karnataka-row-{row_number}")
    return row


def read_input_csv(path: Path, mode: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        rows: list[dict[str, Any]] = []
        used_ids: dict[str, int] = {}
        for row_number, raw in enumerate(reader, start=2):
            if not any(str(v or "").strip() for v in raw.values()):
                continue
            row = canonicalise_row(raw, row_number, mode, used_ids)
            if row["name"]:
                rows.append(row)
        return rows


def candidate_urls(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in WEBSITE_KEYS:
        for url in extract_urls(row.get(key)):
            if url not in urls:
                urls.append(url)
    # Audit queue files sometimes put the useful URL in a generic historical field.
    for key, value in row.items():
        low = str(key).lower()
        if "candidate" in low or "website" in low or "supplied url" in low:
            for url in extract_urls(value):
                if url not in urls:
                    urls.append(url)
    email = row.get("email", "")
    if "@" in email:
        mail_domain = email.rsplit("@", 1)[-1].strip().lower()
        if mail_domain and mail_domain not in FREE_EMAIL_DOMAINS:
            url = normalise_url(mail_domain)
            if url and url not in urls:
                urls.append(url)
    return urls


def identity_aliases(row: dict[str, Any]) -> list[str]:
    values = [row.get("name"), row.get("referral_name"), row.get("public_name"), row.get("project_name"), row.get("parent_organisation")]
    out: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if cleaned and norm(cleaned) not in {norm(x) for x in out}:
            out.append(cleaned)
    stripped = " ".join(tokenise_name(row.get("name")))
    if stripped and norm(stripped) not in {norm(x) for x in out}:
        out.append(stripped)
    acro = acronym(row.get("name"))
    if acro and norm(acro) not in {norm(x) for x in out}:
        out.append(acro)
    return out


def locality_hint(row: dict[str, Any]) -> str:
    address = str(row.get("registered_address") or "")
    chunks = [re.sub(r"\s+", " ", c).strip(" ,-") for c in re.split(r"[,\n]", address)]
    useful = [c for c in chunks if 3 <= len(c) <= 45 and not extract_pincode(c)]
    return useful[-1] if useful else ""


def build_query_plan(row: dict[str, Any], mode: str) -> list[dict[str, str]]:
    spec = MODE_SPECS[mode]
    max_queries = int(spec["max_queries_per_row"])
    if max_queries <= 0:
        return []
    name = row.get("name", "")
    district = row.get("district", "")
    state = row.get("state", "")
    pin = row.get("pincode", "")
    geo = " ".join(x for x in [district, pin, state] if x).strip()
    aliases = identity_aliases(row)
    public_aliases = [a for a in aliases[1:] if norm(a) != norm(name)]
    registration = row.get("registration_reference", "")
    locality = locality_hint(row)
    failed_passes = norm(row.get("failed_query_passes", ""))
    plans: list[dict[str, str]] = []

    def add(query_pass: str, query: str) -> None:
        query = re.sub(r"\s+", " ", query).strip()
        if query and query.lower() not in {p["query"].lower() for p in plans}:
            plans.append({"pass": query_pass, "query": query})

    if mode == "missing_query_only":
        if "public brand" in failed_passes and public_aliases:
            add("missing_public_brand_geo", f'"{public_aliases[0]}" {geo} official website')
        elif "registered name broad" in failed_passes:
            add("missing_registered_name_broad", f'{name} {district} NGO website')
        elif "identifier" in failed_passes and registration:
            add("missing_identifier", f'"{registration}"')
        else:
            add("missing_required_query", f'"{name}" {geo} official website')
        return plans[:1]

    add("legal_name_geo", f'"{name}" {geo} official website')
    if public_aliases:
        add("public_project_parent", f'"{public_aliases[0]}" {district or state} official website')
    elif registration:
        add("registration_identifier", f'"{registration}" {name}')
    else:
        stripped = " ".join(tokenise_name(name))
        add("distinctive_name_geo", f'{stripped or name} {district} NGO')

    if registration:
        add("registration_identifier", f'"{registration}"')
    elif locality:
        add("address_locality", f'"{name}" "{locality}"')
    else:
        acro = acronym(name)
        if acro:
            add("acronym_geo", f'"{acro}" {district} NGO')

    if max_queries >= 4:
        acro = acronym(name)
        if acro:
            add("acronym_or_transliteration", f'"{acro}" {district or state} official')
        tokens = distinctive_tokens(name)
        if tokens:
            add("distinctive_tokens", f'"{" ".join(tokens[:4])}" {district or state}')

    return plans[:max_queries]


def parse_serper_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kg = payload.get("knowledgeGraph") or {}
    if isinstance(kg, dict) and kg.get("website"):
        out.append({
            "url": normalise_url(kg.get("website")), "title": str(kg.get("title") or ""),
            "snippet": str(kg.get("description") or ""), "source": "knowledge_graph", "rank": 0,
        })
    for idx, item in enumerate(payload.get("organic") or [], start=1):
        if not isinstance(item, dict):
            continue
        out.append({
            "url": normalise_url(item.get("link")), "title": str(item.get("title") or ""),
            "snippet": str(item.get("snippet") or ""), "source": "organic", "rank": safe_int(item.get("position"), idx),
        })
    for idx, item in enumerate(payload.get("places") or [], start=1):
        if not isinstance(item, dict):
            continue
        website = item.get("website") or item.get("link")
        if website:
            out.append({
                "url": normalise_url(website), "title": str(item.get("title") or item.get("name") or ""),
                "snippet": " ".join(str(item.get(k) or "") for k in ["address", "phoneNumber", "category"]),
                "source": "places", "rank": idx,
            })
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in out:
        url = candidate.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(candidate)
    return deduped[:12]


def carrier_aliases(candidate: dict[str, Any], row: dict[str, Any]) -> list[str]:
    text = " ".join([candidate.get("title", ""), candidate.get("snippet", "")])
    aliases: list[str] = []
    patterns = [
        r"(?:known as|operates as|branded as|project of|initiative of|run by|operated by|managed by)\s+([A-Z][A-Za-z0-9&' .-]{3,80})",
        r"([A-Z][A-Za-z0-9&' .-]{3,70})\s+(?:is a project|is an initiative|is run|is operated)",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = re.sub(r"\s+", " ", match).strip(" .,-")
            if value and norm(value) != norm(row.get("name")) and value not in aliases:
                aliases.append(value)
    return aliases[:3]


def score_candidate(candidate: dict[str, Any], row: dict[str, Any]) -> tuple[int, str, str]:
    url = candidate.get("url", "")
    title = candidate.get("title", "")
    snippet = candidate.get("snippet", "")
    page_type = page_type_for_candidate(url, title, snippet, row)
    if page_type in {
        "directory_or_registry", "social_media", "article_or_profile", "third_party_mention_candidate",
        "government_academic_or_document_reference", "wrong_entity",
    }:
        reason = obvious_carrier_reason(url, title, snippet, row) or "third-party/carrier page cannot be selected as the NGO website"
        return -1000, page_type, reason
    blob = norm(" ".join([title, snippet, hostname(url), url]))
    aliases = identity_aliases(row)
    legal_norm = norm(row.get("name"))
    core = distinctive_tokens(row.get("name")) or tokenise_name(row.get("name"))
    score = 0
    reasons: list[str] = []
    if legal_norm and legal_norm in blob:
        score += 38
        reasons.append("exact legal name")
    best_alias = next((a for a in aliases[1:] if norm(a) and norm(a) in blob), "")
    if best_alias:
        score += 32
        reasons.append("public/project/parent alias")
    if core:
        overlap = sum(1 for token in core if token in blob) / max(1, len(core))
        score += int(overlap * 28)
        if overlap >= 0.6:
            reasons.append(f"name-token overlap {overlap:.0%}")
    domain_signals = domain_identity_signals(row, url)
    if domain_signals:
        score += 24
        reasons.extend(domain_signals[:2])
    district = norm(row.get("district"))
    pin = row.get("pincode", "")
    registration = norm(row.get("registration_reference"))
    if district and district in blob:
        score += 8
        reasons.append("district")
    if pin and pin in (title + " " + snippet):
        score += 12
        reasons.append("pincode")
    if registration and compact(registration) in compact(blob):
        score += 20
        reasons.append("registration")
    if candidate.get("source") == "knowledge_graph":
        score += 8
        reasons.append("knowledge graph")
    if page_type == "controlled_hosted_microsite_candidate":
        score += 4
    return score, page_type, "; ".join(reasons) or "weak identity signal"


def _jsonld_organisation_metadata(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    names: list[str] = []
    urls: list[str] = []
    for node in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = node.string or node.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
                continue
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            item_type = norm(item.get("@type"))
            if any(t in item_type for t in ["organization", "organisation", "ngo", "educationalorganization", "school"]):
                name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
                url = normalise_url(item.get("url"))
                if name and name not in names:
                    names.append(name)
                if url and url not in urls:
                    urls.append(url)
    return names[:12], urls[:12]


def _structured_owner_names(fetch_metadata: dict[str, Any] | None) -> list[str]:
    if not fetch_metadata:
        return []
    values: list[str] = []
    for key in ("og_site_name", "application_name", "page_title"):
        value = re.sub(r"\s+", " ", str(fetch_metadata.get(key) or "")).strip()
        if value:
            values.append(value)
    for value in fetch_metadata.get("jsonld_org_names") or []:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if value:
            values.append(value)
    footer = re.sub(r"\s+", " ", str(fetch_metadata.get("footer_text") or "")).strip()
    copyright_matches = re.findall(r"(?:copyright|©|all rights reserved)[^|]{0,140}", footer, flags=re.I)
    values.extend(copyright_matches[:5])
    return list(dict.fromkeys(values))


def _alias_overlap(alias: str, value: str) -> float:
    tokens = distinctive_tokens(alias) or [t for t in tokenise_name(alias) if len(t) >= 4]
    blob = norm(value)
    if not tokens:
        return 0.0
    return sum(1 for token in tokens if token in blob) / len(tokens)


def _explicit_relationship(text: str, row: dict[str, Any]) -> bool:
    blob = norm(text)
    aliases = [norm(v) for v in [row.get("name"), row.get("referral_name"), row.get("public_name"), row.get("project_name"), row.get("parent_organisation")] if len(norm(v)) >= 4]
    relation = r"(?:project|initiative|programme|program|unit|school|home|centre|center|institution)\s+(?:of|run by|operated by|managed by)|(?:operated|run|managed|founded|established)\s+by|under the aegis of|part of"
    if not re.search(relation, blob):
        return False
    # A relation phrase elsewhere on a long article is not enough. Require one
    # supplied identity to occur close to a relationship phrase.
    for alias in aliases:
        for m in re.finditer(re.escape(alias), blob):
            window = blob[max(0, m.start()-220): min(len(blob), m.end()+220)]
            if re.search(relation, window):
                return True
    return False


def fetch_direct(url: str, remaining: float) -> dict[str, Any]:
    errors: list[str] = []

    def parse_response(response: Any, variant: str, *, tls_unverified: bool = False) -> dict[str, Any] | None:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "pdf" in content_type or str(response.url).lower().endswith(".pdf"):
            errors.append(f"{variant}: pdf_skipped")
            return None
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            page_title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True) if soup.title else "").strip()
            meta_description = ""
            meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
            if meta and meta.get("content"):
                meta_description = re.sub(r"\s+", " ", str(meta.get("content"))).strip()
            og_site = soup.find("meta", attrs={"property": re.compile(r"^og:site_name$", re.I)})
            og_site_name = re.sub(r"\s+", " ", str(og_site.get("content") or "")).strip() if og_site else ""
            app_meta = soup.find("meta", attrs={"name": re.compile(r"^(application-name|apple-mobile-web-app-title)$", re.I)})
            application_name = re.sub(r"\s+", " ", str(app_meta.get("content") or "")).strip() if app_meta else ""
            canonical = soup.find("link", attrs={"rel": re.compile(r"canonical", re.I)})
            canonical_url = normalise_url(canonical.get("href")) if canonical and canonical.get("href") else ""
            jsonld_org_names, jsonld_org_urls = _jsonld_organisation_metadata(soup)
            mailtos = [str(a.get("href") or "")[7:] for a in soup.select('a[href^="mailto:"]')][:10]
            tels = [str(a.get("href") or "")[4:] for a in soup.select('a[href^="tel:"]')][:10]
            footer_text = " ".join(re.sub(r"\s+", " ", node.get_text(" ", strip=True)) for node in soup.find_all("footer")[:3])[:6000]
            for node in soup(["script", "style", "noscript", "svg"]):
                node.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            low = text.lower()
            js_shell = len(text) < 180 or any(marker in low for marker in [
                "enable javascript", "just a moment", "checking your browser", "captcha", "access denied",
            ])
            return {
                "ok": not js_shell,
                "text": text,
                "page_title": page_title,
                "meta_description": meta_description,
                "og_site_name": og_site_name,
                "application_name": application_name,
                "canonical_url": canonical_url,
                "jsonld_org_names": jsonld_org_names,
                "jsonld_org_urls": jsonld_org_urls,
                "footer_text": footer_text,
                "mailto": " | ".join(mailtos),
                "tel": " | ".join(tels),
                "url": normalise_url(response.url) or variant,
                "status": response.status_code,
                "fetch_status": "javascript_or_challenge" if js_shell else ("direct_ok_tls_unverified" if tls_unverified else "direct_ok"),
                "error": "short/challenge page" if js_shell else ("TLS certificate verification bypassed for public-page read" if tls_unverified else ""),
                "firecrawl_recommended": js_shell,
            }
        if response.status_code in {401, 403, 429, 451, 503}:
            errors.append(f"{variant}: HTTP {response.status_code}")
            return {
                "ok": False, "text": "", "url": variant, "status": response.status_code,
                "fetch_status": "blocked", "error": errors[-1], "firecrawl_recommended": True,
            }
        errors.append(f"{variant}: HTTP {response.status_code}")
        return None

    for variant in url_variants(url, limit=2):
        timeout = max(2.0, min(10.0, remaining - 0.5))
        if timeout <= 1:
            break
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DFP-Karnataka-Recovery/1.0; +https://feedingindia.org)",
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            response = requests.get(variant, headers=headers, timeout=timeout, allow_redirects=True, verify=True)
            parsed = parse_response(response, variant)
            if parsed is not None:
                return parsed
            if response.status_code in {404, 410}:
                continue
        except requests.exceptions.SSLError as exc:
            errors.append(f"{variant}: SSL {str(exc)[:120]}")
            # This is a public, read-only identity check.  A certificate problem
            # should not force paid Firecrawl usage or make a live NGO site look
            # absent.  Retry the same URL once without certificate validation and
            # retain an explicit fetch-status marker in the audit.
            retry_timeout = max(2.0, min(8.0, remaining - 1.0))
            if retry_timeout > 1:
                try:
                    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
                    response = requests.get(variant, headers=headers, timeout=retry_timeout, allow_redirects=True, verify=False)
                    parsed = parse_response(response, variant, tls_unverified=True)
                    if parsed is not None:
                        return parsed
                except requests.RequestException as retry_exc:
                    errors.append(f"{variant}: tls_bypass {type(retry_exc).__name__} {str(retry_exc)[:120]}")
            continue
        except requests.RequestException as exc:
            errors.append(f"{variant}: {type(exc).__name__} {str(exc)[:120]}")
            continue
    return {
        "ok": False, "text": "", "url": normalise_url(url), "status": "failed",
        "fetch_status": "direct_failed", "error": " | ".join(errors[-6:]), "firecrawl_recommended": False,
    }


def identity_verification(
    row: dict[str, Any],
    url: str,
    text: str,
    candidate_type: str,
    candidate_title: str = "",
    candidate_snippet: str = "",
    candidate_source: str = "",
    fetch_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fetch_metadata = fetch_metadata or {}
    combined_text = " ".join([candidate_title, candidate_snippet, text])
    normal_text = norm(combined_text[:160000])
    compact_text = compact(combined_text[:160000])
    aliases = identity_aliases(row)
    legal = norm(row.get("name"))
    core = distinctive_tokens(row.get("name")) or tokenise_name(row.get("name"))
    evidence: list[str] = []
    conflicts: list[str] = []
    ownership_evidence: list[str] = []
    hard = 0
    strong = 0
    supporting = 0

    fetched_title = str(fetch_metadata.get("page_title") or candidate_title or "")
    fetched_description = str(fetch_metadata.get("meta_description") or candidate_snippet or "")
    fetched_type = page_type_for_candidate(url, fetched_title, fetched_description, row)
    carrier_types = {
        "directory_or_registry", "social_media", "article_or_profile", "third_party_mention_candidate",
        "government_academic_or_document_reference", "wrong_entity",
    }
    if fetched_type in carrier_types:
        reason = obvious_carrier_reason(url, fetched_title, fetched_description, row) or "third-party/reference page"
        return {
            "verified": False, "status": "rejected_third_party_or_unowned", "page_type": fetched_type,
            "ownership": "third_party_or_wrong_entity", "confidence": "high", "evidence": "",
            "conflicts": reason, "evidence_score": 0,
        }

    registration = compact(row.get("registration_reference"))
    if registration and len(registration) >= 5 and registration in compact_text:
        hard += 1
        evidence.append("registration reference")
    phone = digits(row.get("phone"))
    if len(phone) >= 8 and phone[-8:] in digits(combined_text):
        hard += 1
        evidence.append("phone")
    email = str(row.get("email") or "").strip().lower()
    if "@" in email and email in combined_text.lower():
        hard += 1
        evidence.append("email")
    pin = row.get("pincode", "")
    if pin and pin in combined_text:
        strong += 1
        evidence.append("pincode")
    district = norm(row.get("district"))
    if district and district in normal_text:
        supporting += 1
        evidence.append("district")
    address_tokens = [t for t in norm(row.get("registered_address")).split() if len(t) >= 5 and t not in STOPWORDS]
    address_hits = [t for t in address_tokens[:14] if t in normal_text]
    if len(address_hits) >= 2:
        strong += 1
        evidence.append("address tokens: " + ", ".join(address_hits[:4]))
    legal_exact = bool(legal and legal in normal_text)
    if legal_exact:
        strong += 2
        evidence.append("exact legal name")
    alias_hit = ""
    for alias in aliases[1:]:
        alias_norm = norm(alias)
        if len(alias_norm) >= 4 and alias_norm in normal_text:
            alias_hit = alias
            strong += 1
            evidence.append("alias/project/parent: " + alias)
            break
    overlap = sum(1 for token in core if token in normal_text) / max(1, len(core)) if core else 0.0
    if overlap >= 0.75:
        strong += 1
        evidence.append(f"name-token overlap {overlap:.0%}")
    elif overlap >= 0.5:
        supporting += 1
        evidence.append(f"partial name-token overlap {overlap:.0%}")

    domain_ev = domain_identity_evidence(row, url)
    domain_strength = safe_int(domain_ev.get("strength"), 0)
    domain_signals = list(domain_ev.get("signals") or [])
    if domain_signals:
        ownership_evidence.extend(domain_signals)
        supporting += 1

    structured_names = _structured_owner_names(fetch_metadata)
    structured_owner_matches: list[str] = []
    structured_conflicts: list[str] = []
    for value in structured_names:
        value_norm = norm(value)
        owner_match = False
        for alias in aliases:
            alias_norm = norm(alias)
            weighted_tokens = distinctive_tokens(alias)
            has_legal_form = any(token in LEGAL_SUFFIXES for token in alias_norm.split())
            exact_full = len(alias_norm) >= 6 and alias_norm in value_norm and (has_legal_form or bool(weighted_tokens))
            weighted_overlap = _alias_overlap(alias, value) if weighted_tokens else 0.0
            if exact_full or weighted_overlap >= 0.75:
                owner_match = True
                break
        if owner_match:
            structured_owner_matches.append(value)
        elif len(distinctive_tokens(value)) >= 1:
            structured_conflicts.append(value)
    if structured_owner_matches:
        ownership_evidence.append("structured page owner matches: " + structured_owner_matches[0][:120])
    explicit_relation = _explicit_relationship(combined_text, row)
    if explicit_relation:
        ownership_evidence.append("explicit project/parent/operator relationship")

    entity_form_present = any(term in normal_text for term in [
        " charitable trust", " foundation", " society", " samsthe", " samiti", " ngo", " non profit",
        " nonprofit", " not for profit", " registered trust", " organisation", " organization",
    ])
    if entity_form_present and (legal_exact or alias_hit):
        supporting += 1
        evidence.append("organisation/legal-form context")

    # Structured metadata naming a different organisation is a conflict unless
    # the page explicitly proves a project/parent relationship.
    if structured_conflicts and not structured_owner_matches and not explicit_relation and domain_strength == 0:
        conflicts.append("page self-brand differs: " + structured_conflicts[0][:120])

    verified_page_type = page_type_after_verification(candidate_type, url, row, combined_text)
    hosted = domain_matches(hostname(url), HOSTED_PLATFORMS)
    parent_project = explicit_relation
    identity_strength = hard * 5 + strong * 2 + supporting
    structured_owner = bool(structured_owner_matches)

    verified = False
    confidence = "low"
    status = "rejected_third_party_or_unowned"
    ownership = "mentions_entity_but_ownership_unproven"

    if hosted:
        # Named subdomain + page identity is required. Opaque hosted URLs remain
        # manual even if the body contains the NGO name.
        if domain_strength >= 2 and (legal_exact or alias_hit or overlap >= 0.75):
            if hard >= 1 or structured_owner or (strong >= 3 and supporting >= 1):
                verified = True
                confidence = "high" if hard or structured_owner else "medium"
            else:
                status = "plausible_site_identity_review"
                ownership = "identity_review_required"
                confidence = "medium" if strong >= 2 else "low"
        elif structured_owner and legal_exact:
            status = "plausible_site_identity_review"
            ownership = "identity_review_required"
            confidence = "medium"
    elif parent_project and (legal_exact or alias_hit or overlap >= 0.75):
        # A parent/project page is valid only when the relationship is explicit;
        # mere co-mention of two names is insufficient.
        verified = True
        confidence = "high" if hard or structured_owner or domain_strength >= 2 else "medium"
    elif domain_strength >= 3 and (legal_exact or alias_hit or overlap >= 0.75):
        if hard >= 1 or structured_owner or entity_form_present or strong >= 3:
            verified = True
            confidence = "high" if hard or structured_owner else "medium"
        else:
            status = "plausible_site_identity_review"
            ownership = "identity_review_required"
            confidence = "medium"
    elif domain_strength == 2 and (legal_exact or alias_hit) and hard >= 1:
        verified = True
        confidence = "high"
    elif domain_strength >= 2 and (legal_exact or alias_hit) and (structured_owner or strong >= 3):
        status = "plausible_site_identity_review"
        ownership = "identity_review_required"
        confidence = "medium"
    elif hard >= 1 and legal_exact and (structured_owner or len(address_hits) >= 2 or bool(pin and pin in combined_text)):
        # Off-brand domains can verify through source-specific contact/legal
        # evidence. This is the only non-parent path that does not require the
        # domain itself to resemble the NGO.
        verified = True
        confidence = "high"

    if verified:
        if hosted:
            status = "verified_controlled_microsite"
            ownership = "controlled_hosted_presence"
            verified_page_type = "controlled_hosted_microsite"
        elif parent_project:
            status = "verified_parent_or_project_page"
            ownership = "verified_parent_project_relationship"
            verified_page_type = "verified_parent_or_project_page"
        else:
            status = "verified_owned_site"
            ownership = "owned_organisation_site"
            verified_page_type = "owned_organisation_site"
    elif status == "rejected_third_party_or_unowned":
        conflicts.append("no independent ownership proof")
        if structured_conflicts:
            conflicts.append("structured owner: " + structured_conflicts[0][:120])

    if ownership_evidence:
        evidence.extend("ownership: " + item for item in ownership_evidence)
    return {
        "verified": verified,
        "status": status,
        "page_type": verified_page_type if verified or status == "plausible_site_identity_review" else "third_party_mention_after_fetch",
        "ownership": ownership,
        "confidence": confidence if verified or status == "plausible_site_identity_review" else "high",
        "evidence": "; ".join(evidence),
        "conflicts": "; ".join(list(dict.fromkeys(conflicts))),
        "evidence_score": identity_strength + domain_strength * 3 + len(ownership_evidence) * 2,
    }


def _domain_list(value: Any) -> list[str]:
    domains: list[str] = []
    for part in re.split(r"[|,;\s]+", str(value or "")):
        part = part.strip().lower()
        if not part:
            continue
        if "://" in part:
            part = hostname(normalise_url(part))
        part = part.lstrip("www.").strip("/")
        if part and "." in part and part not in domains:
            domains.append(part)
    return domains


def _host_matches_any(host: str, domains: list[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def evaluate_regression_result(row: dict[str, Any], result: dict[str, Any]) -> tuple[str, str]:
    expected_raw = str(row.get("expected_outcome") or "").strip()
    expected = norm(expected_raw)
    if not expected:
        return "not_applicable", ""
    status = str(result.get("Discovery Status") or "")
    selected_host = hostname(str(result.get("Website") or ""))
    expected_domains = _domain_list(row.get("regression_expected_domain"))
    forbidden_domains = _domain_list(row.get("regression_forbidden_domains"))
    ownership_evidence = norm(result.get("Identity Evidence"))
    ownership_class = str(result.get("Ownership Class") or "")
    page_type = str(result.get("Page Type") or "")
    dfp_status = norm(result.get("DFP Fit Status"))

    if selected_host and _host_matches_any(selected_host, forbidden_domains):
        return "fail", f"selected forbidden historical/third-party domain: {selected_host}"
    if status in VERIFIED_STATUSES:
        if page_type in {"directory_or_registry", "article_or_profile", "social_media", "third_party_mention_after_fetch", "wrong_entity", "government_academic_or_document_reference"}:
            return "fail", f"verified a non-owned page type: {page_type}"
        if "ownership" not in ownership_evidence:
            return "fail", "verified without a separate ownership signal"
        if ownership_class not in {"owned_organisation_site", "controlled_hosted_presence", "verified_parent_project_relationship"}:
            return "fail", f"verified with invalid ownership class: {ownership_class}"

    if expected_domains and status in VERIFIED_STATUSES and not _host_matches_any(selected_host, expected_domains):
        return "fail", f"selected {selected_host or 'no domain'}; expected one of: {', '.join(expected_domains)}"

    if expected.startswith("retain verified site") or expected.startswith("retain_verified_site"):
        return ("pass", "") if status in VERIFIED_STATUSES else ("fail", f"expected a retained verified site, got {status}")
    if "manual page ownership check" in expected or "manual_page_ownership_check" in expected:
        return ("pass", "") if status in VERIFIED_STATUSES | MANUAL_STATUSES else ("fail", f"expected verified/manual ownership outcome, got {status}")
    if "no owned site after enhanced recovery" in expected or "no_owned_site_after_enhanced_recovery" in expected:
        if status == "no_owned_site_after_enhanced_recovery":
            return "pass", ""
        if status in MANUAL_STATUSES:
            return "pass", "plausible candidate was correctly withheld for human review"
        return "fail", f"no-site control was automatically classified as {status}"
    if "plausible site manual identity review" in expected or "plausible_site_manual_identity_review" in expected:
        return ("pass", "") if status in MANUAL_STATUSES else ("fail", f"expected manual identity review, got {status}")
    if "verified controlled microsite" in expected or "verified_controlled_microsite" in expected:
        return ("pass", "") if status == "verified_controlled_microsite" else ("fail", f"expected controlled microsite, got {status}")
    if "verified parent project page" in expected or "verified_parent_project_page" in expected:
        return ("pass", "") if status == "verified_parent_or_project_page" else ("fail", f"expected parent/project page, got {status}")
    if "verified owned site" in expected or "verified_owned_site" in expected:
        return ("pass", "") if status == "verified_owned_site" else ("fail", f"expected owned site, got {status}")
    if expected.startswith("reject ") or expected.startswith("reject_"):
        if status in VERIFIED_STATUSES | MANUAL_STATUSES | {"no_owned_site_after_enhanced_recovery"}:
            return "pass", ""
        return "fail", f"mismatch recovery did not reach a defensible terminal outcome: {status}"
    if "fetch and verify" in expected or "fetch_and_verify" in expected or "normalise www non www then verify" in expected or "normalise_www_non_www_then_verify" in expected:
        return ("pass", "") if status in VERIFIED_STATUSES else ("fail", f"known live site was not verified: {status}")
    if "retain substantive" in expected or "retain_substantive" in expected:
        if dfp_status == "substantive not fit" or dfp_status == "substantive_not_fit":
            return "pass", ""
        return "fail", f"substantive programme-fit rejection was not preserved separately: {result.get('DFP Fit Status')}"
    if "discover even without" in expected or "discover_even_without" in expected:
        if status in VERIFIED_STATUSES | MANUAL_STATUSES | {"no_owned_site_after_enhanced_recovery"}:
            return "pass", ""
        return "fail", f"not-in-Darpan referral did not complete discovery: {status}"
    return "pass", ""


def blank_result(row: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "NGO ID": row.get("ngo_id") or get_ngo_id(row, context=f"recovery-{row.get('source_record_id', '')}"),
        "Source Record ID": row.get("source_record_id", ""),
        "Source Fingerprint": row.get("source_fingerprint", ""),
        "Source Row Number": row.get("source_row_number", ""),
        "Recovery Mode": mode,
        "Queue Action": row.get("queue_action", ""),
        "NGO Name": row.get("name", ""),
        "State": row.get("state", ""),
        "District": row.get("district", ""),
        "Darpan ID": row.get("darpan_id", ""),
        "Registration Reference": row.get("registration_reference", ""),
        "Registered Address": row.get("registered_address", ""),
        "Pincode": row.get("pincode", ""),
        "Referral Name": row.get("referral_name", ""),
        "Public Name": row.get("public_name", ""),
        "Project Name": row.get("project_name", ""),
        "Parent Organisation": row.get("parent_organisation", ""),
        "Email": row.get("email", ""),
        "Phone": row.get("phone", ""),
        "Sector Tags": row.get("sector_tags", ""),
        "Website": "",
        "Discovery Status": "",
        "Website Status": "",
        "Page Type": "",
        "Ownership Class": "",
        "Confidence": "low",
        "Identity Evidence": "",
        "Identity Conflicts": "",
        "Evidence Page URL": "",
        "Fetch Status": "",
        "Fetch Errors": "",
        "Search Provider": "",
        "Winning Query": "",
        "Query Pass": "",
        "Searched": "no",
        "Logical Queries Used": 0,
        "Provider Attempts": 0,
        "Successful Searches": 0,
        "Failed Searches": 0,
        "Candidate Count": 0,
        "Candidates Verified": 0,
        "Carrier Pages Seen": 0,
        "Firecrawl Credits Used": 0,
        "Firecrawl Action": "",
        "DFP Fit Status": (
            "substantive_not_fit" if "substantive" in norm(row.get("previous_website_status"))
            else (row.get("dfp_fit_status") or "not_yet_assessed")
        ),
        "Previous Website Status": row.get("previous_website_status", ""),
        "Expected Outcome": row.get("expected_outcome", ""),
        "Regression Expected Domain": row.get("regression_expected_domain", ""),
        "Regression Forbidden Domains": row.get("regression_forbidden_domains", ""),
        "Regression Check": "",
        "Regression Failure Reason": "",
        "Retry Required": "no",
        "Retry Reason": "",
        "Note": "",
        "Checked At": utc_now(),
        "Module Version": MODULE_VERSION,
    }


def audit_dict(row: dict[str, Any], mode: str, event: AuditEvent) -> dict[str, Any]:
    return {
        "NGO ID": row.get("ngo_id") or get_ngo_id(row, context=f"audit-{row.get('source_record_id', '')}"),
        "Source Record ID": row.get("source_record_id", ""),
        "NGO Name": row.get("name", ""),
        "District": row.get("district", ""),
        "Recovery Mode": mode,
        "Event Time": utc_now(),
        "Stage": event.stage,
        "Provider": event.provider,
        "Logical Query Number": event.logical_query_number,
        "Provider Attempt Number": event.provider_attempt_number,
        "Query Pass": event.query_pass,
        "Query": event.query,
        "Candidate Rank": event.candidate_rank,
        "Candidate URL": event.candidate_url,
        "Candidate Title": event.candidate_title,
        "Candidate Snippet": event.candidate_snippet,
        "Candidate Source": event.candidate_source,
        "Candidate Domain": hostname(event.candidate_url),
        "Page Type": event.page_type,
        "Candidate Score": event.candidate_score,
        "Decision": event.decision,
        "Reject Reason": event.reject_reason,
        "Fetch Status": event.fetch_status,
        "Fetch Error": event.fetch_error,
        "Evidence": event.evidence,
        "Conflict": event.conflict,
        "Firecrawl Credits Used": event.firecrawl_credits_used,
        "Note": event.note,
    }


def run_ownership_self_test() -> dict[str, Any]:
    """Deterministic, network-free guard against the false-positive classes
    observed in the 44-NGO production regression run.
    """
    failures: list[str] = []
    bad_urls = [
        ("Guardians of Dreams", "https://milaap.org/fundraisers/godreamskochi1"),
        ("Asha Kirana Seva Trust", "https://www.helpyourngo.com/ngo/1570/children/asha-kirana-seva-trust"),
        ("Vimochana Development Society", "https://www.myngos.in/ngo-details/vimochana-development-society-in-karnataka"),
        ("Shivashakthi Foundation", "https://www.tatanexarc.com/company/shree-shivashakthi-innovative-utn5150shr80xqx/"),
        ("Reach Rural Development Society", "https://www.ixigo.com/buses/hyderabad-yadgir-sts"),
        ("The Hope House", "https://pmc.ncbi.nlm.nih.gov/articles/PMC10615235/"),
        ("Auxilium Navajeevana Society", "https://orellsoft.com/client"),
        ("Deenabandhu", "https://rinatham.com/2018/03/13/deenabandhu-chamarajanagar-karnataka/"),
        ("Gonikoppal Higher Primary School", "https://abhyudayakkss.org/school-kit-distribution-at-ghps-kajuru-aigur-village/"),
    ]
    for name, url in bad_urls:
        row = {"name": name, "referral_name": name, "district": "Karnataka", "state": "Karnataka"}
        if page_type_for_candidate(url, row=row) not in {
            "directory_or_registry", "article_or_profile", "third_party_mention_candidate",
            "government_academic_or_document_reference", "wrong_entity",
        }:
            failures.append(f"carrier URL not blocked: {url}")

    synthetic = [
        ({"name": "Shifting Orbits Foundation", "referral_name": "Shifting Orbits Foundation", "state": "Karnataka"},
         "https://www.northsouth.org/", "NorthSouth", "NorthSouth supported a changemaker working with Shifting Orbits Foundation."),
        ({"name": "DIVINE MERCY CHARITABLE TRUST", "referral_name": "Divine Mercy Charitable Trust", "state": "Karnataka"},
         "https://www.divinemercydevotion.net/", "Divine Mercy Devotion", "Divine Mercy devotion prayers and novena. Divine Mercy Charitable Trust is mentioned."),
        ({"name": "VISION INDIA FOUNDATION", "referral_name": "Vision India Trust", "state": "Karnataka"},
         "https://www.giftofvision.org/25-years-of-sankara-eye-foundation-usa", "Sankara Eye Foundation", "An article mentioning Vision India Foundation."),
    ]
    for row, url, owner, body in synthetic:
        result = identity_verification(
            row, url, body, "owned_site_candidate", fetch_metadata={"page_title": owner, "og_site_name": owner, "footer_text": f"© {owner}"}
        )
        if result.get("status") in VERIFIED_STATUSES or result.get("status") in MANUAL_STATUSES:
            failures.append(f"third-party page survived ownership proof: {url} -> {result.get('status')}")

    good = identity_verification(
        {"name": "PRAGATHI CHARITABLE TRUST", "referral_name": "Pragathi Charitable Trust", "district": "Bengaluru Urban", "state": "Karnataka"},
        "https://pragathitrust.org/", "PRAGATHI CHARITABLE TRUST is a registered charitable trust in Bengaluru Urban.",
        "owned_site_candidate", fetch_metadata={"page_title": "Pragathi Charitable Trust", "og_site_name": "Pragathi Charitable Trust", "footer_text": "© Pragathi Charitable Trust"},
    )
    if good.get("status") != "verified_owned_site":
        failures.append(f"official domain failed positive control: {good}")

    hosted = identity_verification(
        {"name": "SADHANA", "referral_name": "Sadhana Raichur", "public_name": "Sadhana Raichur", "district": "Raichur", "state": "Karnataka"},
        "https://sadhana.1ngo.in/", "Sadhana Raichur is a registered NGO working in Raichur.",
        "controlled_hosted_microsite_candidate", fetch_metadata={"page_title": "Sadhana Raichur", "og_site_name": "Sadhana Raichur", "footer_text": "© Sadhana Raichur"},
    )
    if hosted.get("status") != "verified_controlled_microsite":
        failures.append(f"hosted positive control failed: {hosted}")

    parent = identity_verification(
        {"name": "Arsha Gokulam", "referral_name": "Arsha Gokulam", "project_name": "Arsha Gokulam", "parent_organisation": "Arsha Seva Kendram", "state": "Karnataka"},
        "https://www.arshasevakendram.org/seva/arsha-gokulam/", "Arsha Gokulam is a project of Arsha Seva Kendram.",
        "parent_or_project_candidate", fetch_metadata={"page_title": "Arsha Gokulam | Arsha Seva Kendram", "og_site_name": "Arsha Seva Kendram", "footer_text": "© Arsha Seva Kendram"},
    )
    if parent.get("status") != "verified_parent_or_project_page":
        failures.append(f"parent/project positive control failed: {parent}")

    return {"passed": not failures, "failures": failures, "cases": len(bad_urls) + len(synthetic) + 3, "module_version": MODULE_VERSION}


class KarnatakaRecoveryService:
    def __init__(
        self,
        runs_dir: Path,
        max_upload_bytes: int,
        avika_callback: Callable[[Path, str], dict[str, Any]] | None = None,
    ):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes
        self.avika_callback = avika_callback
        self.router = APIRouter(prefix="/karnataka-recovery", tags=["Karnataka Recovery"])
        self.threads: dict[str, threading.Thread] = {}
        self.controls: dict[str, dict[str, threading.Event]] = {}
        self.lock = threading.RLock()
        self.serper_preflight_cache: dict[str, dict[str, Any]] = {}
        self.firecrawl_preflight_cache: dict[str, dict[str, Any]] = {}
        self.ownership_self_test = run_ownership_self_test()
        self._register_routes()

    def _run_dir(self, run_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "", run_id)
        return self.runs_dir / safe

    def _json(self, ok: bool, status_code: int = 200, **payload: Any) -> JSONResponse:
        return JSONResponse({"ok": ok, **payload}, status_code=status_code)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)

    def _status(self, rd: Path) -> dict[str, Any]:
        return self._read_json(rd / RESULT_FILES["status"])

    def _write_status(self, rd: Path, **changes: Any) -> dict[str, Any]:
        current = self._status(rd)
        current.update(changes)
        current["updated_at"] = utc_now()
        current["downloads"] = {kind: (rd / filename).exists() for kind, filename in RESULT_FILES.items() if kind not in {"status", "settings"}}
        self._write_json(rd / RESULT_FILES["status"], current)
        return current

    @staticmethod
    def _append_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _rewrite_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _init_outputs(self, rd: Path) -> None:
        self._rewrite_csv(rd / RESULT_FILES["results"], RESULT_FIELDS, [])
        self._rewrite_csv(rd / RESULT_FILES["audit"], AUDIT_FIELDS, [])
        self._rewrite_csv(rd / RESULT_FILES["query_plan"], QUERY_PLAN_FIELDS, [])
        (rd / RESULT_FILES["errors"]).touch(exist_ok=True)

    def _load_results(self, rd: Path) -> list[dict[str, Any]]:
        path = rd / RESULT_FILES["results"]
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _processed_source_ids(self, rd: Path) -> set[str]:
        return {str(row.get("Source Record ID") or "") for row in self._load_results(rd) if str(row.get("Source Record ID") or "")}

    def _checkpoint(self, rd: Path, row: dict[str, Any], mode: str, result: dict[str, Any], events: list[AuditEvent]) -> None:
        if mode == "regression_test":
            check, reason = evaluate_regression_result(row, result)
            result["Regression Check"] = check
            result["Regression Failure Reason"] = reason
        self._append_csv(rd / RESULT_FILES["results"], RESULT_FIELDS, [result])
        self._append_csv(rd / RESULT_FILES["audit"], AUDIT_FIELDS, [audit_dict(row, mode, event) for event in events])
        if result.get("Website") and result.get("Discovery Status") in VERIFIED_STATUSES:
            self._append_csv(
                rd / RESULT_FILES["avika_input"],
                ["ngo_id", "name", "district", "state", "darpan_id", "website", "website_recovery_status", "source_record_id"],
                [{
                    "ngo_id": result.get("NGO ID", ""),
                    "name": result.get("NGO Name", ""), "district": result.get("District", ""), "state": result.get("State", ""),
                    "darpan_id": result.get("Darpan ID", ""), "website": result.get("Website", ""),
                    "website_recovery_status": result.get("Discovery Status", ""), "source_record_id": result.get("Source Record ID", ""),
                }],
            )

    def _query_plan_rows(self, rows: list[dict[str, Any]], selected_mode: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            mode = row.get("recovery_mode") if row.get("recovery_mode") in MODE_SPECS else selected_mode
            plans = build_query_plan(row, mode)
            if not plans:
                out.append({
                    "NGO ID": row.get("ngo_id") or get_ngo_id(row), "Source Record ID": row.get("source_record_id"), "NGO Name": row.get("name"), "District": row.get("district"),
                    "Recovery Mode": mode, "Maximum Logical Queries": 0, "Query Number": 0, "Query Pass": "zero_query",
                    "Query": "", "Uses Existing URL First": "yes", "Candidate URL Count": len(candidate_urls(row)),
                })
            else:
                for index, plan in enumerate(plans, start=1):
                    out.append({
                        "NGO ID": row.get("ngo_id") or get_ngo_id(row), "Source Record ID": row.get("source_record_id"), "NGO Name": row.get("name"), "District": row.get("district"),
                        "Recovery Mode": mode, "Maximum Logical Queries": MODE_SPECS[mode]["max_queries_per_row"], "Query Number": index,
                        "Query Pass": plan["pass"], "Query": plan["query"], "Uses Existing URL First": "yes",
                        "Candidate URL Count": len(candidate_urls(row)),
                    })
        return out

    def _reserve_logical_query(self, shared: dict[str, Any]) -> None:
        with shared["lock"]:
            cap = int(shared.get("query_cap") or 0)
            if cap and int(shared.get("logical_queries") or 0) >= cap:
                raise QueryCapReached("run logical query cap reached")
            shared["logical_queries"] = int(shared.get("logical_queries") or 0) + 1

    def _verify_one_candidate(
        self,
        ctx: RowContext,
        candidate: dict[str, Any],
        use_firecrawl: bool,
        firecrawl_pool: FirecrawlPool | None,
        query_info: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        ctx.check_deadline()
        ctx.candidates_verified += 1
        ctx.best_candidate = dict(candidate)
        url = candidate.get("url", "")
        candidate_type = candidate.get("page_type") or page_type_for_candidate(url, candidate.get("title", ""), candidate.get("snippet", ""), ctx.row)
        direct = fetch_direct(url, ctx.remaining())
        firecrawl_action = ""
        firecrawl_credits = 0
        fetched = direct
        if (not direct.get("ok")) and use_firecrawl and firecrawl_pool and direct.get("firecrawl_recommended") and ctx.remaining() > 8:
            firecrawl_action = "scrape_after_" + str(direct.get("fetch_status") or "direct_failure")
            try:
                fc = firecrawl_pool.scrape(url, timeout_sec=max(10, min(45, int(ctx.remaining() - 2))))
            except ProviderUnavailable as exc:
                if ctx.mode == "firecrawl_retry":
                    raise
                firecrawl_action = f"not_run_{exc.reason}"
                fc = {"ok": False, "credits": 0, "error": str(exc)}
            firecrawl_credits = safe_int(fc.get("credits"), 0)
            ctx.firecrawl_credits_used += firecrawl_credits
            if fc.get("ok"):
                fetched = {"ok": True, "text": fc.get("text", ""), "url": normalise_url(fc.get("url")) or url, "status": 200, "fetch_status": "firecrawl_ok", "error": "", "firecrawl_recommended": False}
            else:
                fetched = {**direct, "error": " | ".join(x for x in [direct.get("error", ""), str(fc.get("error") or "")] if x)}
        event_base = AuditEvent(
            stage="candidate_verification", provider=(query_info or {}).get("provider", "zero_query"),
            logical_query_number=safe_int((query_info or {}).get("logical_query_number")), query_pass=(query_info or {}).get("pass", ""),
            query=(query_info or {}).get("query", ""), candidate_rank=safe_int(candidate.get("rank")), candidate_url=url,
            candidate_title=candidate.get("title", ""), candidate_snippet=candidate.get("snippet", ""), candidate_source=candidate.get("source", ""),
            page_type=candidate_type, candidate_score=candidate.get("score", ""), fetch_status=fetched.get("fetch_status", ""),
            fetch_error=fetched.get("error", ""), firecrawl_credits_used=firecrawl_credits, note=firecrawl_action,
        )
        if not fetched.get("ok"):
            event_base.decision = "candidate_fetch_pending"
            ctx.audit.append(event_base)
            return {
                "url": fetched.get("url") or url, "fetch_ok": False, "fetch_status": fetched.get("fetch_status", "direct_failed"),
                "fetch_error": fetched.get("error", ""), "status": "candidate_fetch_pending", "page_type": candidate_type,
                "ownership": "unverified_due_to_fetch", "confidence": "low", "evidence": "", "conflicts": "",
                "firecrawl_action": firecrawl_action, "firecrawl_credits": firecrawl_credits,
            }
        verification_text = " ".join([
            str(fetched.get("page_title") or ""), str(fetched.get("meta_description") or ""),
            str(fetched.get("mailto") or ""), str(fetched.get("tel") or ""), str(fetched.get("text") or ""),
        ])
        verification = identity_verification(
            ctx.row, fetched.get("url") or url, verification_text, candidate_type,
            candidate_title=str(candidate.get("title") or fetched.get("page_title") or ""),
            candidate_snippet=str(candidate.get("snippet") or fetched.get("meta_description") or ""),
            candidate_source=str(candidate.get("source") or ""),
            fetch_metadata=fetched,
        )
        ctx.best_verification = {**verification, "url": fetched.get("url") or url, "fetch_status": fetched.get("fetch_status", "direct_ok"), "fetch_error": fetched.get("error", "")}
        if verification.get("verified"):
            event_base.decision = "accepted"
        elif verification.get("status") == "rejected_third_party_or_unowned":
            event_base.decision = "rejected_after_fetch_ownership_unproven"
        else:
            event_base.decision = "manual_identity_review"
        event_base.page_type = verification.get("page_type", candidate_type)
        event_base.evidence = verification.get("evidence", "")
        event_base.conflict = verification.get("conflicts", "")
        ctx.audit.append(event_base)
        return {
            "url": fetched.get("url") or url, "fetch_ok": True, "fetch_status": fetched.get("fetch_status", "direct_ok"),
            "fetch_error": fetched.get("error", ""), "status": verification.get("status"), "page_type": verification.get("page_type"),
            "ownership": verification.get("ownership"), "confidence": verification.get("confidence"), "evidence": verification.get("evidence"),
            "conflicts": verification.get("conflicts"), "evidence_score": verification.get("evidence_score", 0),
            "firecrawl_action": firecrawl_action, "firecrawl_credits": firecrawl_credits,
        }

    def _result_from_verification(
        self,
        ctx: RowContext,
        verification: dict[str, Any],
        candidate: dict[str, Any],
        query_info: dict[str, str] | None,
        note: str,
    ) -> dict[str, Any]:
        result = blank_result(ctx.row, ctx.mode)
        status = verification.get("status") or "plausible_site_identity_review"
        result.update({
            "Website": verification.get("url", ""),
            "Discovery Status": status,
            "Website Status": status,
            "Page Type": verification.get("page_type", ""),
            "Ownership Class": verification.get("ownership", ""),
            "Confidence": verification.get("confidence", "low"),
            "Identity Evidence": verification.get("evidence", ""),
            "Identity Conflicts": verification.get("conflicts", ""),
            "Evidence Page URL": verification.get("url", ""),
            "Fetch Status": verification.get("fetch_status", ""),
            "Fetch Errors": verification.get("fetch_error", ""),
            "Search Provider": (query_info or {}).get("provider", "zero_query"),
            "Winning Query": (query_info or {}).get("query", ""),
            "Query Pass": (query_info or {}).get("pass", ""),
            "Searched": "yes" if ctx.logical_queries_used else "no",
            "Logical Queries Used": ctx.logical_queries_used,
            "Provider Attempts": ctx.provider_attempts,
            "Successful Searches": ctx.successful_searches,
            "Failed Searches": ctx.failed_searches,
            "Candidate Count": ctx.candidate_count,
            "Candidates Verified": ctx.candidates_verified,
            "Carrier Pages Seen": ctx.carrier_pages_seen,
            "Firecrawl Credits Used": ctx.firecrawl_credits_used,
            "Firecrawl Action": verification.get("firecrawl_action", ""),
            "Retry Required": "yes" if status in RETRY_STATUSES else "no",
            "Retry Reason": verification.get("fetch_error", "") if status in RETRY_STATUSES else "",
            "Note": note,
        })
        return result

    def _process_row(
        self,
        row: dict[str, Any],
        selected_mode: str,
        serper_pool: SerperPool | None,
        firecrawl_pool: FirecrawlPool | None,
        shared: dict[str, Any],
        use_firecrawl: bool,
        row_deadline_sec: int,
    ) -> tuple[dict[str, Any], list[AuditEvent]]:
        mode = row.get("recovery_mode") if row.get("recovery_mode") in MODE_SPECS else selected_mode
        spec = MODE_SPECS[mode]
        ctx = RowContext(row=row, mode=mode, deadline_at=time.monotonic() + max(20, row_deadline_sec), max_queries=int(spec["max_queries_per_row"]))
        direct_candidates: list[dict[str, Any]] = []
        all_uploaded_urls: set[str] = set()
        carrier_types = {
            "directory_or_registry", "social_media", "article_or_profile", "third_party_mention_candidate",
            "government_academic_or_document_reference", "wrong_entity",
        }
        for index, url in enumerate(candidate_urls(row), start=1):
            all_uploaded_urls.add(url)
            candidate_type = page_type_for_candidate(url, row=row)
            if candidate_type in carrier_types:
                ctx.carrier_pages_seen += 1
                ctx.audit.append(AuditEvent(
                    stage="zero_query_candidate", provider="zero_query", candidate_rank=index, candidate_url=url,
                    page_type=candidate_type, decision="carrier_only_continue",
                    reject_reason=obvious_carrier_reason(url, row=row) or "third-party page is never official ownership evidence",
                ))
                continue
            direct_candidates.append({"url": url, "title": "", "snippet": "", "source": "uploaded_candidate", "rank": index, "page_type": candidate_type, "score": 100})

        best_manual: tuple[dict[str, Any], dict[str, Any], dict[str, str] | None] | None = None
        best_unreachable: tuple[dict[str, Any], dict[str, Any], dict[str, str] | None] | None = None
        for candidate in direct_candidates:
            verification = self._verify_one_candidate(ctx, candidate, use_firecrawl or mode == "firecrawl_retry", firecrawl_pool, None)
            if verification.get("status") in VERIFIED_STATUSES:
                return self._result_from_verification(ctx, verification, candidate, None, "Verified from an existing URL without a Serper query."), ctx.audit
            if verification.get("status") == "candidate_fetch_pending":
                if not best_unreachable:
                    best_unreachable = (verification, candidate, None)
            elif verification.get("status") in MANUAL_STATUSES:
                if not best_manual or safe_int(verification.get("evidence_score")) > safe_int(best_manual[0].get("evidence_score")):
                    best_manual = (verification, candidate, None)
            elif verification.get("status") == "rejected_third_party_or_unowned":
                ctx.carrier_pages_seen += 1

        if ctx.max_queries <= 0:
            if best_manual:
                verification, candidate, qinfo = best_manual
                status = "collision_identity_review" if mode == "identity_collision" else "plausible_site_identity_review"
                verification = {**verification, "status": status}
                return self._result_from_verification(ctx, verification, candidate, qinfo, "Existing candidate needs human identity confirmation; no search query was spent."), ctx.audit
            if best_unreachable:
                verification, candidate, qinfo = best_unreachable
                return self._result_from_verification(ctx, verification, candidate, qinfo, "Existing candidate could not be fetched. Use the Firecrawl retry queue; do not classify this as no website."), ctx.audit
            result = blank_result(row, mode)
            result.update({
                "Discovery Status": "no_candidate_in_uploaded_row", "Website Status": "no_candidate_in_uploaded_row",
                "Note": "This zero-query queue contained no usable candidate URL. Move the row to enhanced search; this is not a no-site conclusion.",
                "Retry Required": "yes", "Retry Reason": "enhanced_search_required",
                "Candidate Count": ctx.candidate_count, "Candidates Verified": ctx.candidates_verified, "Carrier Pages Seen": ctx.carrier_pages_seen,
            })
            return result, ctx.audit

        if not serper_pool:
            raise ProviderUnavailable("serper", "not_configured_for_search_mode")

        plans = build_query_plan(row, mode)
        seen_urls = set(all_uploaded_urls)
        carrier_hints: list[str] = []
        for query_index, plan in enumerate(plans, start=1):
            ctx.check_deadline()
            self._reserve_logical_query(shared)
            ctx.logical_queries_used += 1
            qinfo = {"provider": "serper", "query": plan["query"], "pass": plan["pass"], "logical_query_number": str(query_index)}
            try:
                payload, attempts = serper_pool.search(plan["query"], timeout=max(8, min(20, int(ctx.remaining() - 1))))
                ctx.provider_attempts += len(attempts)
                for attempt in attempts:
                    ctx.audit.append(AuditEvent(
                        stage="provider_attempt", provider="serper", logical_query_number=query_index,
                        provider_attempt_number=safe_int(attempt.get("attempt")), query_pass=plan["pass"], query=plan["query"],
                        decision="success" if safe_int(attempt.get("status")) == 200 else "failed_over",
                        note=f"key {attempt.get('key')} status {attempt.get('status')}",
                    ))
                ctx.successful_searches += 1
            except ProviderUnavailable:
                raise
            except Exception as exc:
                ctx.failed_searches += 1
                ctx.audit.append(AuditEvent(stage="logical_search", provider="serper", logical_query_number=query_index, query_pass=plan["pass"], query=plan["query"], decision="search_failed", note=str(exc)[:300]))
                continue

            candidates = parse_serper_candidates(payload)
            ctx.candidate_count += len(candidates)
            scored: list[dict[str, Any]] = []
            for candidate in candidates:
                if candidate.get("url") in seen_urls:
                    continue
                seen_urls.add(candidate.get("url"))
                score, candidate_type, reason = score_candidate(candidate, row)
                candidate.update({"score": score, "page_type": candidate_type, "score_reason": reason})
                if score == -1000:
                    ctx.carrier_pages_seen += 1
                    carrier_hints.extend(carrier_aliases(candidate, row))
                    ctx.audit.append(AuditEvent(
                        stage="candidate_classification", provider="serper", logical_query_number=query_index, query_pass=plan["pass"], query=plan["query"],
                        candidate_rank=candidate.get("rank", 0), candidate_url=candidate.get("url", ""), candidate_title=candidate.get("title", ""),
                        candidate_snippet=candidate.get("snippet", ""), candidate_source=candidate.get("source", ""), page_type=candidate_type,
                        candidate_score=score, decision="carrier_only_continue", reject_reason=reason,
                    ))
                    continue
                if score < 16:
                    ctx.audit.append(AuditEvent(
                        stage="candidate_classification", provider="serper", logical_query_number=query_index, query_pass=plan["pass"], query=plan["query"],
                        candidate_rank=candidate.get("rank", 0), candidate_url=candidate.get("url", ""), candidate_title=candidate.get("title", ""),
                        candidate_snippet=candidate.get("snippet", ""), candidate_source=candidate.get("source", ""), page_type=candidate_type,
                        candidate_score=score, decision="rejected_weak_identity", reject_reason=reason,
                    ))
                    continue
                scored.append(candidate)
                ctx.audit.append(AuditEvent(
                    stage="candidate_classification", provider="serper", logical_query_number=query_index, query_pass=plan["pass"], query=plan["query"],
                    candidate_rank=candidate.get("rank", 0), candidate_url=candidate.get("url", ""), candidate_title=candidate.get("title", ""),
                    candidate_snippet=candidate.get("snippet", ""), candidate_source=candidate.get("source", ""), page_type=candidate_type,
                    candidate_score=score, decision="nominated_for_verification", note=reason,
                ))
            scored.sort(key=lambda item: (safe_int(item.get("score")), -safe_int(item.get("rank"), 99)), reverse=True)
            for candidate in scored[:10]:
                verification = self._verify_one_candidate(ctx, candidate, use_firecrawl, firecrawl_pool, qinfo)
                if verification.get("status") in VERIFIED_STATUSES:
                    return self._result_from_verification(ctx, verification, candidate, qinfo, "Verified after staged search. Third-party candidates were retained only as carrier evidence and the workflow continued."), ctx.audit
                if verification.get("status") == "candidate_fetch_pending":
                    if not best_unreachable or safe_int(candidate.get("score")) > safe_int(best_unreachable[1].get("score")):
                        best_unreachable = (verification, candidate, qinfo)
                elif verification.get("status") in MANUAL_STATUSES:
                    if not best_manual or safe_int(verification.get("evidence_score")) > safe_int(best_manual[0].get("evidence_score")):
                        best_manual = (verification, candidate, qinfo)
                elif verification.get("status") == "rejected_third_party_or_unowned":
                    ctx.carrier_pages_seen += 1

            # One targeted carrier recovery query is allowed only if it fits the
            # mode's existing query ceiling. It replaces, rather than adds to,
            # the final generic plan.
            if carrier_hints and query_index < len(plans):
                hint = carrier_hints[0]
                plans[query_index] = {"pass": "carrier_alias_recovery", "query": f'"{hint}" {row.get("district") or row.get("state")} official website'}

        if best_manual:
            verification, candidate, qinfo = best_manual
            status = "collision_identity_review" if mode == "identity_collision" else "plausible_site_identity_review"
            verification = {**verification, "status": status}
            return self._result_from_verification(ctx, verification, candidate, qinfo, "A plausible owned/controlled page was found but the supplied identity evidence was not strong enough for automatic acceptance."), ctx.audit
        if best_unreachable:
            verification, candidate, qinfo = best_unreachable
            return self._result_from_verification(ctx, verification, candidate, qinfo, "A plausible candidate was retained but could not be fetched. Retry only the URL; do not repeat the Serper search first."), ctx.audit

        result = blank_result(row, mode)
        if ctx.successful_searches == 0 and ctx.failed_searches > 0:
            status = "search_partial"
            note = "All intended logical searches failed for technical reasons. Rerun this row; no no-site conclusion was made."
        elif ctx.failed_searches > 0:
            status = "search_partial"
            note = "At least one required logical search failed. Rerun the missing stage before making a no-site conclusion."
        else:
            status = "no_owned_site_after_enhanced_recovery"
            note = "No owned, controlled, parent or project page was verified after the mode's completed staged search. Directory/article/donor pages were not accepted as official websites."
        result.update({
            "Discovery Status": status, "Website Status": status, "Searched": "yes", "Logical Queries Used": ctx.logical_queries_used,
            "Provider Attempts": ctx.provider_attempts, "Successful Searches": ctx.successful_searches, "Failed Searches": ctx.failed_searches,
            "Candidate Count": ctx.candidate_count, "Candidates Verified": ctx.candidates_verified, "Carrier Pages Seen": ctx.carrier_pages_seen,
            "Firecrawl Credits Used": ctx.firecrawl_credits_used, "Retry Required": "yes" if status == "search_partial" else "no",
            "Retry Reason": "failed_logical_search" if status == "search_partial" else "", "Note": note,
        })
        return result, ctx.audit

    def _write_derived_exports(self, rd: Path) -> dict[str, int]:
        results = self._load_results(rd)
        manual = [row for row in results if row.get("Discovery Status") in MANUAL_STATUSES]
        no_site = [row for row in results if row.get("Discovery Status") == "no_owned_site_after_enhanced_recovery"]
        retry = [row for row in results if row.get("Discovery Status") in RETRY_STATUSES or str(row.get("Retry Required")).lower() == "yes"]
        self._rewrite_csv(rd / RESULT_FILES["manual_review"], RESULT_FIELDS, manual)
        self._rewrite_csv(rd / RESULT_FILES["no_site"], RESULT_FIELDS, no_site)
        retry_fields = [
            "ngo_id", "source_record_id", "source_fingerprint", "name", "district", "state", "darpan_id", "registration_reference",
            "registered_address", "pincode", "referral_name", "public_name", "project_name", "parent_organisation", "email", "phone",
            "recheck_candidate_url", "previous_discovery_status", "retry_reason", "recovery_mode_override",
        ]
        retry_rows = [{
            "ngo_id": row.get("NGO ID", ""), "source_record_id": row.get("Source Record ID", ""), "source_fingerprint": row.get("Source Fingerprint", ""),
            "name": row.get("NGO Name", ""), "district": row.get("District", ""), "state": row.get("State", ""),
            "darpan_id": row.get("Darpan ID", ""), "registration_reference": row.get("Registration Reference", ""),
            "registered_address": row.get("Registered Address", ""), "pincode": row.get("Pincode", ""),
            "referral_name": row.get("Referral Name", ""), "public_name": row.get("Public Name", ""), "project_name": row.get("Project Name", ""),
            "parent_organisation": row.get("Parent Organisation", ""), "email": row.get("Email", ""), "phone": row.get("Phone", ""),
            "recheck_candidate_url": row.get("Website", ""), "previous_discovery_status": row.get("Discovery Status", ""),
            "retry_reason": row.get("Retry Reason", ""),
            "recovery_mode_override": "firecrawl_retry" if row.get("Discovery Status") == "candidate_fetch_pending" else "missing_query_only" if row.get("Discovery Status") == "search_partial" else row.get("Recovery Mode", ""),
        } for row in retry]
        self._rewrite_csv(rd / RESULT_FILES["retry"], retry_fields, retry_rows)
        return {"manual_review": len(manual), "no_site": len(no_site), "retry": len(retry)}

    def _summarise(self, rd: Path, settings: dict[str, Any], shared: dict[str, Any], serper_pool: SerperPool | None, firecrawl_pool: FirecrawlPool | None, started_at: float) -> dict[str, Any]:
        results = self._load_results(rd)
        by_status: dict[str, int] = {}
        for row in results:
            status = str(row.get("Discovery Status") or "unknown")
            by_status[status] = by_status.get(status, 0) + 1
        derived = self._write_derived_exports(rd)
        regression_counts: dict[str, int] = {}
        for row in results:
            check = str(row.get("Regression Check") or "not_applicable")
            regression_counts[check] = regression_counts.get(check, 0) + 1
        regression_failures = [
            {"ngo_id": row.get("NGO ID", ""), "name": row.get("NGO Name", ""), "reason": row.get("Regression Failure Reason", "")}
            for row in results if row.get("Regression Check") == "fail"
        ]
        summary = {
            "module_version": MODULE_VERSION,
            "run_id": rd.name,
            "mode": settings.get("mode"),
            "total_input_rows": settings.get("total_rows", 0),
            "processed_rows": len(results),
            "remaining_rows": max(0, int(settings.get("total_rows", 0)) - len(results)),
            "discovery_status_counts": by_status,
            "verified_websites": sum(by_status.get(status, 0) for status in VERIFIED_STATUSES),
            "manual_review": derived["manual_review"],
            "no_owned_site_after_enhanced_recovery": derived["no_site"],
            "retry_rows": derived["retry"],
            "logical_queries_used": int(shared.get("logical_queries") or 0),
            "provider_attempts": int(serper_pool.provider_attempts if serper_pool else 0),
            "serper_preflight_queries": int(serper_pool.preflight_queries if serper_pool else 0),
            "firecrawl_credits_used": int(firecrawl_pool.used if firecrawl_pool else 0),
            "requested_concurrency": settings.get("requested_concurrency"),
            "effective_concurrency": settings.get("effective_concurrency"),
            "query_cap": settings.get("query_cap"),
            "elapsed_seconds": round(max(0.0, time.time() - started_at), 1),
            "serper_key_stats": serper_pool.stats() if serper_pool else [],
            "serper_account_stats": serper_pool.stats() if serper_pool else [],
            "serper_credit_budget": settings.get("serper_credit_budget", 0),
            "serper_concurrency": settings.get("serper_concurrency", settings.get("serper_per_key_concurrency", 0)),
            "firecrawl_key_stats": firecrawl_pool.stats() if firecrawl_pool else [],
            "regression_status_counts": regression_counts,
            "regression_passed": bool(settings.get("mode") != "regression_test" or not regression_failures),
            "production_run_allowed": bool(settings.get("mode") != "regression_test" or not regression_failures),
            "regression_failures": regression_failures[:100],
            "ownership_self_test": self.ownership_self_test,
            "generated_at": utc_now(),
        }
        self._write_json(rd / RESULT_FILES["summary"], summary)
        return summary

    def _run_job(self, run_id: str) -> None:
        rd = self._run_dir(run_id)
        settings = self._read_json(rd / RESULT_FILES["settings"])
        selected_mode = str(settings.get("mode") or "enhanced_search")
        rows = read_input_csv(rd / RESULT_FILES["input"], selected_mode)
        processed_ids = self._processed_source_ids(rd)
        pending = [row for row in rows if row.get("source_record_id") not in processed_ids]
        controls = self.controls.setdefault(run_id, {"pause": threading.Event(), "cancel": threading.Event()})
        controls["pause"].clear()
        controls["cancel"].clear()
        started_at = time.time()
        query_cap = safe_int(settings.get("query_cap"), 0)
        shared = {"lock": threading.RLock(), "logical_queries": safe_int(settings.get("logical_queries_before_resume"), 0), "query_cap": query_cap}
        serper_pool: SerperPool | None = None
        firecrawl_pool: FirecrawlPool | None = None

        try:
            serper_keys = parse_env_keys("SERPER_API_KEYS", "SERPER_API_KEY")
            if any(MODE_SPECS[row.get("recovery_mode", selected_mode)]["requires_serper"] for row in pending):
                serper_pool = SerperPool(serper_keys, safe_int(settings.get("serper_concurrency") or settings.get("serper_per_key_concurrency"), 4), self.serper_preflight_cache)
                stats = serper_pool.preflight(bool(settings.get("preflight", True)))
                if serper_pool.healthy_count() <= 0:
                    self._write_status(rd, run_status="paused", stage="provider_capacity_unavailable", message="The configured Serper account did not pass preflight. Add credits or replace SERPER_API_KEY, then resume.", serper_key_stats=stats, can_resume=True, can_pause=False, can_cancel=False)
                    return
            firecrawl_enabled = bool(settings.get("use_firecrawl")) or selected_mode == "firecrawl_retry"
            if firecrawl_enabled:
                firecrawl_pool = FirecrawlPool(
                    parse_env_keys("FIRECRAWL_API_KEYS", "FIRECRAWL_API_KEY"),
                    safe_int(settings.get("firecrawl_budget"), 0), self.firecrawl_preflight_cache,
                    str(settings.get("firecrawl_proxy") or "basic"),
                )
                firecrawl_stats = firecrawl_pool.preflight()
                if selected_mode == "firecrawl_retry" and not any(s.get("state") == "healthy" for s in firecrawl_stats):
                    self._write_status(rd, run_status="paused", stage="firecrawl_capacity_unavailable", message="Firecrawl retry was selected but no healthy funded Firecrawl key is available.", firecrawl_key_stats=firecrawl_stats, can_resume=True, can_pause=False, can_cancel=False)
                    return

            requested = max(1, safe_int(settings.get("requested_concurrency"), MODE_SPECS[selected_mode]["default_concurrency"]))
            if serper_pool:
                effective = min(requested, max(1, serper_pool.healthy_count() * safe_int(settings.get("serper_concurrency") or settings.get("serper_per_key_concurrency"), 4)), 32)
            elif selected_mode == "firecrawl_retry":
                healthy_fc = sum(1 for s in (firecrawl_pool.stats() if firecrawl_pool else []) if s.get("state") == "healthy")
                effective = min(requested, max(1, healthy_fc * 2), 8)
            else:
                effective = min(requested, 24)
            settings["effective_concurrency"] = effective
            settings["logical_queries_before_resume"] = int(shared.get("logical_queries") or 0)
            self._write_json(rd / RESULT_FILES["settings"], settings)
            total = len(rows)
            already = len(processed_ids)
            self._write_status(
                rd, run_status="running", stage="processing", processed=already, total=total, remaining=max(0, total - already),
                progress_pct=round(already / total * 100, 2) if total else 100.0, requested_concurrency=requested,
                effective_concurrency=effective, current_item="Starting queue", can_pause=True, can_cancel=True, can_resume=False,
                serper_key_stats=serper_pool.stats() if serper_pool else [], firecrawl_key_stats=firecrawl_pool.stats() if firecrawl_pool else [],
                query_cap=query_cap, queries_used=int(shared.get("logical_queries") or 0), firecrawl_credits_used=firecrawl_pool.used if firecrawl_pool else 0,
            )

            pending_iter = iter(pending)
            active: dict[Future, dict[str, Any]] = {}
            completed = already
            last_status_write = 0.0
            provider_pause: ProviderUnavailable | None = None

            with ThreadPoolExecutor(max_workers=effective, thread_name_prefix="karnataka-recovery") as executor:
                def submit_next() -> bool:
                    if controls["pause"].is_set() or controls["cancel"].is_set() or provider_pause is not None:
                        return False
                    try:
                        row = next(pending_iter)
                    except StopIteration:
                        return False
                    future = executor.submit(
                        self._process_row, row, selected_mode, serper_pool, firecrawl_pool, shared,
                        bool(settings.get("use_firecrawl")) or selected_mode == "firecrawl_retry",
                        safe_int(settings.get("row_deadline_seconds"), 90),
                    )
                    active[future] = row
                    return True

                for _ in range(min(effective, len(pending))):
                    submit_next()

                while active:
                    done, _ = wait(list(active), timeout=0.5, return_when=FIRST_COMPLETED)
                    if not done:
                        if controls["cancel"].is_set() or controls["pause"].is_set():
                            continue
                        now = time.time()
                        if now - last_status_write >= 2:
                            self._write_status(rd, current_item=f"{len(active)} source records in flight", queries_used=int(shared.get("logical_queries") or 0), serper_key_stats=serper_pool.stats() if serper_pool else [], firecrawl_credits_used=firecrawl_pool.used if firecrawl_pool else 0)
                            last_status_write = now
                        continue
                    for future in done:
                        row = active.pop(future)
                        try:
                            result, events = future.result()
                            self._checkpoint(rd, row, result.get("Recovery Mode") or selected_mode, result, events)
                            completed += 1
                        except ProviderUnavailable as exc:
                            provider_pause = exc
                            with (rd / RESULT_FILES["errors"]).open("a", encoding="utf-8") as handle:
                                handle.write(f"{utc_now()} {row.get('source_record_id')} {row.get('name')} :: {exc}\n")
                        except QueryCapReached as exc:
                            result = blank_result(row, row.get("recovery_mode") or selected_mode)
                            result.update({"Discovery Status": "skipped_query_cap", "Website Status": "skipped_query_cap", "Retry Required": "yes", "Retry Reason": "query_cap", "Note": str(exc)})
                            self._checkpoint(rd, row, result.get("Recovery Mode") or selected_mode, result, [])
                            completed += 1
                        except RowDeadlineReached as exc:
                            ctx = exc.ctx
                            result = blank_result(row, ctx.mode)
                            candidate = ctx.best_candidate or {}
                            verification = ctx.best_verification or {}
                            result.update({
                                "Website": verification.get("url") or candidate.get("url", ""),
                                "Discovery Status": "candidate_fetch_pending" if candidate else "search_partial",
                                "Website Status": "candidate_fetch_pending" if candidate else "search_partial",
                                "Page Type": verification.get("page_type") or candidate.get("page_type", ""),
                                "Ownership Class": verification.get("ownership", "unverified_due_to_deadline"),
                                "Identity Evidence": verification.get("evidence", ""),
                                "Identity Conflicts": verification.get("conflicts", ""),
                                "Fetch Status": verification.get("fetch_status", "row_deadline"),
                                "Fetch Errors": verification.get("fetch_error", ""),
                                "Searched": "yes" if ctx.logical_queries_used else "no",
                                "Logical Queries Used": ctx.logical_queries_used,
                                "Provider Attempts": ctx.provider_attempts,
                                "Successful Searches": ctx.successful_searches,
                                "Failed Searches": ctx.failed_searches,
                                "Candidate Count": ctx.candidate_count,
                                "Candidates Verified": ctx.candidates_verified,
                                "Carrier Pages Seen": ctx.carrier_pages_seen,
                                "Firecrawl Credits Used": ctx.firecrawl_credits_used,
                                "Retry Required": "yes", "Retry Reason": "row_deadline",
                                "Note": "Row deadline reached. The best candidate, counters and audit events were preserved; retry only the unfinished stage.",
                            })
                            self._checkpoint(rd, row, ctx.mode, result, ctx.audit)
                            completed += 1
                        except Exception as exc:
                            result = blank_result(row, row.get("recovery_mode") or selected_mode)
                            result.update({"Discovery Status": "search_partial", "Website Status": "search_partial", "Retry Required": "yes", "Retry Reason": "unexpected_error", "Note": str(exc)[:350]})
                            self._checkpoint(rd, row, result.get("Recovery Mode") or selected_mode, result, [])
                            completed += 1
                            with (rd / RESULT_FILES["errors"]).open("a", encoding="utf-8") as handle:
                                handle.write(f"{utc_now()} {row.get('source_record_id')} {row.get('name')} :: {type(exc).__name__}: {exc}\n")

                        progress = round(completed / total * 100, 2) if total else 100.0
                        elapsed = max(0.1, time.time() - started_at)
                        rate = max(0.0, (completed - already) / elapsed * 60)
                        remaining = max(0, total - completed)
                        eta = int(remaining / rate * 60) if rate > 0 else None
                        self._write_status(
                            rd, processed=completed, total=total, remaining=remaining, progress_pct=progress,
                            current_item=row.get("name", ""), queries_used=int(shared.get("logical_queries") or 0),
                            provider_attempts=serper_pool.provider_attempts if serper_pool else 0,
                            firecrawl_credits_used=firecrawl_pool.used if firecrawl_pool else 0,
                            throughput_rows_per_min=round(rate, 2), eta_seconds=eta,
                            serper_key_stats=serper_pool.stats() if serper_pool else [], firecrawl_key_stats=firecrawl_pool.stats() if firecrawl_pool else [],
                        )
                        if not controls["pause"].is_set() and not controls["cancel"].is_set() and provider_pause is None:
                            submit_next()

                    if provider_pause is not None:
                        # Let already-started rows finish/checkpoint, but do not submit more.
                        continue

            summary = self._summarise(rd, settings, shared, serper_pool, firecrawl_pool, started_at)
            avika_info: dict[str, Any] = {"filter_status": "not_requested", "repository_rows": 0}
            if bool(settings.get("run_avika")) and self.avika_callback and (rd / RESULT_FILES["avika_input"]).exists():
                self._write_status(rd, stage="avika_filtering", current_item="Running DFP fit / Avika classification on verified websites")
                try:
                    avika_info = self.avika_callback(rd, "karnataka") or avika_info
                except Exception as exc:
                    avika_info = {"filter_status": "error", "error": str(exc)[:500], "repository_rows": 0}
                summary["avika_filter"] = avika_info
                self._write_json(rd / RESULT_FILES["summary"], summary)

            if provider_pause:
                final_status, stage = "paused", "provider_capacity_unavailable"
                message = f"Paused safely because {provider_pause.provider} has no usable capacity. Completed source records are checkpointed."
                can_resume = True
            elif controls["pause"].is_set():
                final_status, stage = "paused", "user_paused"
                message = "Paused after all already-started source records were checkpointed."
                can_resume = True
            elif controls["cancel"].is_set():
                final_status, stage = "cancelled", "cancelled_partial_results_saved"
                message = "Cancelled. Completed source records and downloads were retained."
                can_resume = True
            else:
                final_status, stage = "complete", "results_ready"
                message = "Karnataka recovery completed. Discovery and DFP-fit statuses remain separate."
                can_resume = False
            self._write_status(
                rd, run_status=final_status, stage=stage, message=message, summary=summary,
                processed=summary.get("processed_rows"), total=summary.get("total_input_rows"), remaining=summary.get("remaining_rows"),
                progress_pct=round((summary.get("processed_rows", 0) / max(1, summary.get("total_input_rows", 1))) * 100, 2),
                queries_used=summary.get("logical_queries_used", 0), provider_attempts=summary.get("provider_attempts", 0),
                firecrawl_credits_used=summary.get("firecrawl_credits_used", 0), can_pause=False, can_cancel=False, can_resume=can_resume,
                current_item="", avika_filter=avika_info, filtered_repository_rows=safe_int(avika_info.get("repository_rows")),
            )
        except Exception as exc:
            with (rd / RESULT_FILES["errors"]).open("a", encoding="utf-8") as handle:
                handle.write(f"{utc_now()} fatal :: {type(exc).__name__}: {exc}\n")
            self._write_status(rd, run_status="error", stage="error", error=str(exc)[:500], message="Karnataka recovery stopped because of an unexpected error.", can_resume=True, can_pause=False, can_cancel=False)

    def _register_routes(self) -> None:
        router = self.router

        @router.get("/modes")
        def modes() -> JSONResponse:
            return self._json(True, module_version=MODULE_VERSION, modes=MODE_SPECS, ownership_self_test=self.ownership_self_test)

        @router.get("/ownership-self-test")
        def ownership_self_test() -> JSONResponse:
            return self._json(bool(self.ownership_self_test.get("passed")), status_code=200 if self.ownership_self_test.get("passed") else 503, **self.ownership_self_test)

        @router.get("/capacity")
        def capacity(
            serper_concurrency: int = 0,
            serper_per_key_concurrency: int = 4,
            include_firecrawl: bool = False,
            firecrawl_budget: int = 5000,
        ) -> JSONResponse:
            account_concurrency = max(1, min(int(serper_concurrency or serper_per_key_concurrency or 4), 8))
            serper_keys = parse_env_keys("SERPER_API_KEYS", "SERPER_API_KEY")
            serper_stats: list[dict[str, Any]] = []
            healthy_serper = 0
            if serper_keys:
                pool = SerperPool(serper_keys, account_concurrency, self.serper_preflight_cache)
                serper_stats = pool.preflight(True)
                healthy_serper = pool.healthy_count()
            firecrawl_keys = parse_env_keys("FIRECRAWL_API_KEYS", "FIRECRAWL_API_KEY")
            firecrawl_stats: list[dict[str, Any]] = []
            if include_firecrawl and firecrawl_keys:
                firecrawl_pool = FirecrawlPool(
                    firecrawl_keys, max(1, min(int(firecrawl_budget), 100000)), self.firecrawl_preflight_cache, "basic"
                )
                firecrawl_stats = firecrawl_pool.preflight()
            healthy_firecrawl = sum(1 for row in firecrawl_stats if row.get("state") == "healthy")
            return self._json(
                True,
                module_version=MODULE_VERSION,
                serper_configured=bool(serper_keys),
                serper_account_configured=bool(serper_keys),
                serper_key_count=len(serper_keys),
                healthy_serper_keys=healthy_serper,
                healthy_serper_accounts=healthy_serper,
                serper_per_key_concurrency=account_concurrency,
                serper_concurrency=account_concurrency,
                recommended_max_concurrency=healthy_serper * account_concurrency,
                serper_key_stats=serper_stats,
                serper_account_stats=serper_stats,
                configuration_warning=serper_account_warning(),
                firecrawl_configured=bool(firecrawl_keys),
                healthy_firecrawl_keys=healthy_firecrawl,
                firecrawl_key_stats=firecrawl_stats,
                ownership_self_test=self.ownership_self_test,
            )

        @router.post("/start")
        async def start(
            file: UploadFile = File(...),
            mode: str = "enhanced_search",
            concurrency: int = 12,
            serper_concurrency: int = 0,
            serper_per_key_concurrency: int = 4,
            serper_credit_budget: int = 59000,
            query_cap: int = 0,
            preflight: bool = True,
            use_firecrawl: bool = False,
            firecrawl_budget: int = 0,
            firecrawl_proxy: str = "basic",
            run_avika: bool = False,
            row_deadline_seconds: int = 90,
        ) -> JSONResponse:
            mode = str(mode or "").strip().lower()
            if not self.ownership_self_test.get("passed"):
                return self._json(False, 503, error="The built-in strict ownership self-test failed; this worker refuses to run.", ownership_self_test=self.ownership_self_test)
            if mode not in MODE_SPECS:
                return self._json(False, 400, error=f"Unknown mode. Choose one of: {', '.join(MODE_SPECS)}")
            with self.lock:
                active = [run_id for run_id, thread in self.threads.items() if thread.is_alive()]
            if active:
                return self._json(False, 409, error="Another Karnataka Recovery run is active", active_runs=active)
            data = await file.read(self.max_upload_bytes + 1)
            if len(data) > self.max_upload_bytes:
                return self._json(False, 413, error=f"CSV exceeds the {self.max_upload_bytes:,}-byte upload limit")
            run_id = f"karnataka_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
            rd = self._run_dir(run_id)
            rd.mkdir(parents=True, exist_ok=True)
            (rd / RESULT_FILES["input"]).write_bytes(data)
            try:
                rows = read_input_csv(rd / RESULT_FILES["input"], mode)
            except Exception as exc:
                return self._json(False, 400, error=f"Could not read CSV: {exc}")
            if not rows:
                return self._json(False, 400, error="CSV contains no NGO rows. Include a name/ngo_name column.")
            if len(rows) > 30000:
                return self._json(False, 400, error="A single Karnataka Recovery upload is capped at 30,000 rows. Use the prepared split files.")
            serper_keys = parse_env_keys("SERPER_API_KEYS", "SERPER_API_KEY")
            needs_serper = any(MODE_SPECS[row.get("recovery_mode", mode)]["requires_serper"] for row in rows)
            if needs_serper and not serper_keys:
                return self._json(False, 503, error="This mode requires one funded SERPER_API_KEY.")
            if (use_firecrawl or mode == "firecrawl_retry") and firecrawl_budget <= 0:
                return self._json(False, 400, error="Firecrawl was enabled but the per-run Firecrawl credit budget is zero.")
            if (use_firecrawl or mode == "firecrawl_retry") and not parse_env_keys("FIRECRAWL_API_KEYS", "FIRECRAWL_API_KEY"):
                return self._json(False, 503, error="Firecrawl requires FIRECRAWL_API_KEY or FIRECRAWL_API_KEYS.")
            maximum_queries = sum(int(MODE_SPECS[row.get("recovery_mode", mode)]["max_queries_per_row"]) for row in rows)
            credit_budget = max(0, min(int(serper_credit_budget or 0), 1000000))
            requested_query_cap = max(0, int(query_cap))
            effective_query_cap = requested_query_cap or maximum_queries
            if needs_serper and credit_budget:
                # Leave one query of headroom for the account preflight when enabled.
                usable_budget = max(0, credit_budget - (1 if preflight else 0))
                effective_query_cap = min(effective_query_cap, usable_budget)
            account_concurrency = max(1, min(int(serper_concurrency or serper_per_key_concurrency or 4), 8))
            settings = {
                "module_version": MODULE_VERSION,
                "mode": mode,
                "total_rows": len(rows),
                "input_filename": file.filename or "uploaded_input.csv",
                "requested_concurrency": max(1, min(int(concurrency), 64)),
                "serper_concurrency": account_concurrency,
                "serper_per_key_concurrency": account_concurrency,
                "serper_credit_budget": credit_budget,
                "query_cap": effective_query_cap,
                "estimated_maximum_queries": maximum_queries,
                "preflight": bool(preflight),
                "use_firecrawl": bool(use_firecrawl),
                "firecrawl_budget": max(0, min(int(firecrawl_budget), 100000)),
                "firecrawl_proxy": firecrawl_proxy if firecrawl_proxy in {"basic", "auto", "enhanced"} else "basic",
                "run_avika": bool(run_avika),
                "row_deadline_seconds": max(20, min(int(row_deadline_seconds), 240)),
                "created_at": utc_now(),
            }
            self._write_json(rd / RESULT_FILES["settings"], settings)
            self._init_outputs(rd)
            self._rewrite_csv(rd / RESULT_FILES["query_plan"], QUERY_PLAN_FIELDS, self._query_plan_rows(rows, mode))
            self._write_status(
                rd, ok=True, module="karnataka_recovery", module_version=MODULE_VERSION, run_id=run_id, run_status="starting",
                stage="queued", mode=mode, mode_label=MODE_SPECS[mode]["label"], total=len(rows), processed=0, remaining=len(rows),
                progress_pct=0.0, requested_concurrency=settings["requested_concurrency"], effective_concurrency=None,
                query_cap=effective_query_cap, estimated_maximum_queries=maximum_queries, queries_used=0, provider_attempts=0,
                firecrawl_budget=settings["firecrawl_budget"], firecrawl_credits_used=0, run_avika=settings["run_avika"],
                serper_credit_budget=settings["serper_credit_budget"], serper_concurrency=settings["serper_concurrency"],
                serper_account_configured=bool(serper_keys), configuration_warning=serper_account_warning(),
                can_pause=True, can_cancel=True, can_resume=False, message="Queued Karnataka Recovery",
            )
            self.controls[run_id] = {"pause": threading.Event(), "cancel": threading.Event()}
            thread = threading.Thread(target=self._run_job, args=(run_id,), daemon=True, name=f"karnataka-{run_id[-6:]}")
            with self.lock:
                self.threads[run_id] = thread
            thread.start()
            return self._json(True, run_id=run_id, total=len(rows), mode=mode, estimated_maximum_queries=maximum_queries, query_cap=effective_query_cap)

        @router.get("/status/{run_id}")
        def status(run_id: str) -> JSONResponse:
            rd = self._run_dir(run_id)
            if not rd.exists():
                return self._json(False, 404, error="Karnataka Recovery run not found")
            payload = self._status(rd)
            thread = self.threads.get(run_id)
            payload["thread_alive"] = bool(thread and thread.is_alive())
            if payload.get("run_status") in {"starting", "running"} and not payload["thread_alive"]:
                payload["run_status"] = "interrupted"
                payload["stage"] = "interrupted_restart"
                payload["can_resume"] = True
            return self._json(True, **payload)

        @router.post("/pause/{run_id}")
        def pause(run_id: str) -> JSONResponse:
            controls = self.controls.get(run_id)
            thread = self.threads.get(run_id)
            if not controls or not thread or not thread.is_alive():
                return self._json(False, 409, error="Run is not active")
            controls["pause"].set()
            self._write_status(self._run_dir(run_id), run_status="pause_requested", stage="pause_requested", message="Finishing and checkpointing already-started source records before pausing.")
            return self._json(True, run_id=run_id, stage="pause_requested")

        @router.post("/cancel/{run_id}")
        def cancel(run_id: str) -> JSONResponse:
            controls = self.controls.get(run_id)
            thread = self.threads.get(run_id)
            if not controls or not thread or not thread.is_alive():
                return self._json(False, 409, error="Run is not active")
            controls["cancel"].set()
            self._write_status(self._run_dir(run_id), run_status="cancel_requested", stage="cancel_requested", message="Finishing and checkpointing already-started source records before stopping.")
            return self._json(True, run_id=run_id, stage="cancel_requested")

        @router.post("/resume/{run_id}")
        def resume(run_id: str) -> JSONResponse:
            rd = self._run_dir(run_id)
            if not rd.exists() or not (rd / RESULT_FILES["input"]).exists():
                return self._json(False, 404, error="Saved input for this run is missing")
            thread = self.threads.get(run_id)
            if thread and thread.is_alive():
                return self._json(False, 409, error="Run is already active")
            with self.lock:
                active = [rid for rid, th in self.threads.items() if rid != run_id and th.is_alive()]
            if active:
                return self._json(False, 409, error="Another Karnataka Recovery run is active", active_runs=active)
            old = self._status(rd)
            settings = self._read_json(rd / RESULT_FILES["settings"])
            settings["logical_queries_before_resume"] = safe_int(old.get("queries_used"), 0)
            settings["resume_count"] = safe_int(settings.get("resume_count"), 0) + 1
            self._write_json(rd / RESULT_FILES["settings"], settings)
            self.controls[run_id] = {"pause": threading.Event(), "cancel": threading.Event()}
            self._write_status(rd, run_status="resuming", stage="resume_started", can_resume=False, can_pause=True, can_cancel=True, message="Resuming from source-record checkpoints.")
            thread = threading.Thread(target=self._run_job, args=(run_id,), daemon=True, name=f"karnataka-{run_id[-6:]}")
            with self.lock:
                self.threads[run_id] = thread
            thread.start()
            return self._json(True, run_id=run_id, stage="resumed")

        @router.get("/runs")
        def runs(limit: int = 50) -> JSONResponse:
            rows: list[dict[str, Any]] = []
            for rd in sorted(self.runs_dir.glob("karnataka_*"), key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(limit, 200))]:
                status_payload = self._status(rd)
                if status_payload:
                    rows.append(status_payload)
            return self._json(True, runs=rows)

        @router.get("/export/{run_id}/{kind}")
        def export(run_id: str, kind: str):
            rd = self._run_dir(run_id)
            filename = RESULT_FILES.get(kind)
            if not filename:
                return self._json(False, 404, error=f"Unknown export kind: {kind}")
            path = rd / filename
            if not path.exists():
                return self._json(False, 404, error=f"Export is not ready: {kind}")
            media = "application/json" if path.suffix == ".json" else "text/plain" if path.suffix == ".log" else "text/csv"
            return FileResponse(path, media_type=media, filename=path.name)


def build_karnataka_recovery_router(
    *,
    runs_dir: Path,
    max_upload_bytes: int,
    avika_callback: Callable[[Path, str], dict[str, Any]] | None = None,
) -> APIRouter:
    return KarnatakaRecoveryService(runs_dir, max_upload_bytes, avika_callback).router
