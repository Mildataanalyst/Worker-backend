"""Stable DFP NGO identifiers shared by discovery, shortlisting and outreach.

The ID is deterministic so the search worker and core backend can assign the same
identifier without sharing a database. Existing valid IDs are always preserved.

Seed priority deliberately favours immutable source/lead identifiers over loose
registration descriptors. The Karnataka Darpan extract contains registration
values that are reused across unrelated rows, so a registration value alone is
never treated as globally unique.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping
from urllib.parse import urlparse

NGO_ID_VERSION = "dfp_ngo_id_v2"
NGO_ID_PREFIX = "DFP-NGO-"
NGO_ID_RE = re.compile(r"^DFP-NGO-[A-F0-9]{16}$", re.I)

ID_KEYS = ("ngo_id", "NGO ID", "dfp_ngo_id", "DFP NGO ID", "unique_ngo_id", "Unique NGO ID")
DARPA_KEYS = ("darpan_id", "Darpan ID", "ngo_darpan_id", "NGO Darpan ID", "unique_id", "Unique ID")
REG_KEYS = (
    "registration_reference", "Registration Reference", "registration_descriptor", "Registration Descriptor",
    "registration_no", "Registration No", "registration_number", "Registration Number",
)
DOMAIN_KEYS = (
    "website", "Website", "Official Website", "url", "URL", "Website / Source", "Source URL",
    "Evidence Page URL", "recheck_candidate_url", "candidate_url",
)
SOURCE_KEYS = ("source_record_id", "Source Record ID", "source_id", "Source ID")
LEAD_KEYS = ("lead_id", "Lead ID")
NAME_KEYS = ("ngo_name", "NGO Name", "name", "Name", "Organisation", "Organization", "organisation", "organization", "input_name")
DISTRICT_KEYS = ("district", "District", "Location", "location")
STATE_KEYS = ("state", "State", "region", "Region")

_PLACEHOLDER_IDENTIFIERS = {
    "", "0", "-", "na", "n a", "n/a", "nan", "nil", "none", "null",
    "not available", "not applicable", "unknown", "missing",
}


def _first(row: Mapping[str, Any] | None, keys: tuple[str, ...]) -> str:
    if not isinstance(row, Mapping):
        return ""
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalise_identity_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\bwww\.", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _usable_identifier(value: Any) -> str:
    normalised = normalise_identity_text(value)
    return "" if normalised in _PLACEHOLDER_IDENTIFIERS else normalised


def canonical_domain(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        host = (urlparse(raw).hostname or "").strip().lower().rstrip(".")
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    return host


def existing_ngo_id(row: Mapping[str, Any] | None) -> str:
    raw = _first(row, ID_KEYS).upper()
    return raw if NGO_ID_RE.fullmatch(raw) else ""


def ngo_identity_seed(row: Mapping[str, Any] | None, *, context: str = "") -> str:
    row = row or {}

    # A true NGO Darpan Unique ID is the strongest cross-export identifier.
    darpan = _usable_identifier(_first(row, DARPA_KEYS))
    if darpan:
        return f"darpan|{darpan}"

    # The source ledger ID is immutable for a Darpan row and prevents same-name,
    # same-district organisations from being collapsed. It intentionally outranks
    # the registration descriptor because the supplied Karnataka master reuses
    # short registration values across unrelated organisations.
    source_record_id = _usable_identifier(_first(row, SOURCE_KEYS))
    if source_record_id:
        return f"source|{source_record_id}"

    # Lead Pool IDs are stable once an NGO enters the discovery/shortlisting path.
    lead_id = _usable_identifier(_first(row, LEAD_KEYS))
    if lead_id:
        return f"lead|{lead_id}"

    name = normalise_identity_text(_first(row, NAME_KEYS))
    district = normalise_identity_text(_first(row, DISTRICT_KEYS))
    state = normalise_identity_text(_first(row, STATE_KEYS))

    # Registration is fallback evidence, not a standalone unique key. Bind it to
    # legal/public name and location so generic values such as "22" cannot merge
    # unrelated NGOs.
    registration = _usable_identifier(_first(row, REG_KEYS))
    if registration:
        return f"registration_name_location|{registration}|{name}|{district}|{state}"

    domain = ""
    for key in DOMAIN_KEYS:
        domain = canonical_domain(row.get(key))
        if domain:
            break
    if domain:
        return f"domain|{domain}"

    if name:
        return f"name_location|{name}|{district}|{state}"

    fallback = normalise_identity_text(context) or "unidentified"
    return f"fallback|{fallback}"


def generate_ngo_id(row: Mapping[str, Any] | None, *, context: str = "") -> str:
    current = existing_ngo_id(row)
    if current:
        return current
    seed = f"{NGO_ID_VERSION}|{ngo_identity_seed(row, context=context)}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16].upper()
    return f"{NGO_ID_PREFIX}{digest}"


def ensure_ngo_id(row: dict[str, Any], *, context: str = "", field: str = "ngo_id") -> str:
    ngo_id = generate_ngo_id(row, context=context)
    row[field] = ngo_id
    return ngo_id


def get_ngo_id(row: Mapping[str, Any] | None, *, context: str = "") -> str:
    return generate_ngo_id(row, context=context)
