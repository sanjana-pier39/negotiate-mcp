"""nash_validation — pre-charge validation for Nash orders.

Runs BEFORE creating a Stripe Checkout Session so we never charge a customer
for an order the merchant will reject (typo'd zip, malformed email, missing
state). All validation is structural — no third-party API calls, no network,
no signups. Catches ~80% of bad inputs at zero cost; the rest (non-existent
street addresses, dead email accounts) are caught by the 24h auto-refund SLA.

Public surface:
    validate_email(raw)             → (ok, normalized, error_or_None)
    validate_us_address(raw_dict)   → (ok, normalized_dict, errors_list)
    validate_order_inputs(email, address) → (ok, normalized_dict, errors_list)

If we later add a paid validator (Smarty, Google Address Validation, USPS),
wrap it as a second pass that runs only when its API key env var is set.
Structural validation always runs first as a cheap fail-fast.
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

# Practical RFC 5322 — permissive enough for real-world emails, strict enough
# to catch the typos LLMs actually make. Rejects:
#   - "sanjana@pier39"      (no TLD)
#   - "sanjana.pier39.ai"   (no @)
#   - "sanjana @pier39.ai"  (whitespace)
#   - "sanjana@@pier39.ai"  (double @)
#   - "@pier39.ai"          (no local part)
#   - "sanjana@.com"        (leading dot in domain)
#   - "sanjana@pier39..ai"  (double dot in domain)
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+"          # local: letters, digits, ._%+-
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)+"  # domain labels
    r"[A-Za-z]{2,24}$"              # TLD 2-24 chars (covers .museum, .photography)
)


def validate_email(raw: str) -> tuple[bool, str, Optional[str]]:
    """Validate + normalize an email address.

    Returns (ok, normalized, error_msg). Normalized = trimmed, lowercased.
    """
    if not raw or not isinstance(raw, str):
        return False, "", "email is empty"
    email = raw.strip()
    if not email:
        return False, "", "email is empty"
    if " " in email or "\t" in email or "\n" in email:
        return False, email, "email contains whitespace"
    if email.count("@") != 1:
        return False, email, "email must contain exactly one @"
    local, _, domain = email.partition("@")
    if not local:
        return False, email, "email is missing the part before @"
    if not domain:
        return False, email, "email is missing the domain after @"
    if ".." in email:
        return False, email, "email contains consecutive dots"
    if email.startswith(".") or email.endswith(".") or local.endswith("."):
        return False, email, "email starts or ends with a dot"
    if "." not in domain:
        return False, email, f"email domain '{domain}' is missing a TLD (e.g. '.com', '.ai')"
    if len(email) > 254:
        return False, email, "email is longer than 254 characters"
    normalized = email.lower()
    if not _EMAIL_RE.match(normalized):
        return False, email, f"email '{email}' is not a valid format"
    return True, normalized, None


# ---------------------------------------------------------------------------
# US Address
# ---------------------------------------------------------------------------

# 50 states + DC + 5 territories (US Postal Service abbreviations).
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
    # Territories (USPS-deliverable)
    "PR", "VI", "GU", "AS", "MP",
}

# Full state name → 2-letter code, for normalization when LLM sends
# "California" instead of "CA".
US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
    "virgin islands": "VI", "guam": "GU", "american samoa": "AS",
    "northern mariana islands": "MP",
}

# ZIP: 5 digits, optionally followed by -4 digits. Leading zeros are valid
# (00501 is real — Holtsville, NY, an IRS address).
_ZIP_RE = re.compile(r"^(\d{5})(?:-(\d{4}))?$")


def _normalize_state(raw: str) -> Optional[str]:
    """Return canonical 2-letter US state code, or None if unrecognized."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # 2-letter code path
    if len(s) == 2:
        upper = s.upper()
        return upper if upper in US_STATES else None
    # Full name path
    return US_STATE_NAMES.get(s.lower())


def _normalize_zip(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Return (canonical_zip, error_msg). Canonical = '94105' or '94105-1234'.

    Accepts: "94105", "94105-1234", "94105 1234", "941051234", or any
    of the above with leading/trailing whitespace.
    """
    if not raw:
        return None, "zip is empty"
    z = re.sub(r"\s+", "", str(raw).strip())
    if not z:
        return None, "zip is empty"
    # If we got exactly 9 digits, treat as ZIP+4 with the dash dropped.
    if re.fullmatch(r"\d{9}", z):
        z = f"{z[:5]}-{z[5:]}"
    m = _ZIP_RE.match(z)
    if not m:
        return None, f"zip '{raw}' is not a valid US ZIP (expected 5 digits or 5+4)"
    return (f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)), None


# Supported ship-to countries. US uses the strict US validator; the rest use
# the lighter international validator (no US state/ZIP rules).
_COUNTRY_ALIASES = {
    "US": "US", "USA": "US", "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "GB": "GB", "UK": "GB", "UNITED KINGDOM": "GB", "GREAT BRITAIN": "GB",
    "IE": "IE", "IRELAND": "IE",
    "DE": "DE", "GERMANY": "DE", "DEUTSCHLAND": "DE",
    "FR": "FR", "FRANCE": "FR",
    "ES": "ES", "SPAIN": "ES", "ESPAÑA": "ES",
    "IT": "IT", "ITALY": "IT", "ITALIA": "IT",
    "NL": "NL", "NETHERLANDS": "NL",
    "BE": "BE", "BELGIUM": "BE",
    "AT": "AT", "AUSTRIA": "AT",
    "PT": "PT", "PORTUGAL": "PT",
}


def _normalize_country(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a country to a supported ISO-2 code. Returns (canonical, err)."""
    if not raw:
        return "US", None  # default
    c = _COUNTRY_ALIASES.get(raw.strip().upper())
    if c:
        return c, None
    return None, (
        f"country '{raw}' is not supported yet — Nash ships to the US, UK, and "
        f"the EU (DE, FR, IE, ES, IT, NL, BE, AT, PT)."
    )


def validate_intl_address(raw: dict) -> tuple[bool, dict, list[str]]:
    """Validate + normalize a non-US (UK/EU) shipping address. Lighter than the
    US validator: no 'state' concept, postal formats vary by country, so we
    only require a street line, city, a postal code (in the `zip` field), and a
    supported country. Returns (ok, normalized_dict, errors)."""
    errors: list[str] = []
    country, cerr = _normalize_country(raw.get("country") or "")
    if cerr:
        errors.append(cerr)
    norm = {
        "line1": (raw.get("line1") or "").strip(),
        "line2": (raw.get("line2") or "").strip(),
        "city":  (raw.get("city")  or "").strip(),
        # Keep whatever region/county the caller sent (optional abroad).
        "state": (raw.get("state") or "").strip(),
        # Postal code travels in the `zip` field across the codebase.
        "zip":   (raw.get("zip")   or "").strip(),
        "country": country or (raw.get("country") or "").strip().upper(),
    }
    if len(norm["line1"]) < 4:
        errors.append("shipping_address_line1 is too short — need a street address")
    if len(norm["city"]) < 2:
        errors.append("shipping_city is required")
    if len(norm["zip"]) < 3:
        errors.append("postal code (shipping_zip) is required")
    return (len(errors) == 0), norm, errors


def validate_us_address(raw: dict) -> tuple[bool, dict, list[str]]:
    """Validate + normalize a US shipping address.

    Input dict keys: line1, line2 (optional), city, state, zip, country (optional).
    Returns (ok, normalized_dict, errors_list).

    Normalization:
      - Strips whitespace from all fields
      - Title-cases city
      - Uppercases state (CA, not ca)
      - Resolves full state names to 2-letter codes (California → CA)
      - Normalizes country aliases (USA → US)
      - Standardizes ZIP format (94105 or 94105-1234)
    """
    errors: list[str] = []
    norm = {
        "line1": (raw.get("line1") or "").strip(),
        "line2": (raw.get("line2") or "").strip(),
        "city":  (raw.get("city")  or "").strip(),
        "state": (raw.get("state") or "").strip(),
        "zip":   (raw.get("zip")   or "").strip(),
        "country": (raw.get("country") or "").strip(),
    }

    # --- line1: must look like a real street line ---
    if not norm["line1"]:
        errors.append("shipping_address_line1 is empty — need a street address")
    elif len(norm["line1"]) < 8:
        # Real US street lines: "1 Apple Park Way"=16, "500 Folsom St"=13,
        # "10 NW St"=8. Anything shorter is almost certainly a fragment
        # like "Apt 4B" or a partial address.
        errors.append(
            f"shipping_address_line1 '{norm['line1']}' is too short — "
            f"need a full street address like '123 Main St'"
        )
    else:
        # Heuristic: real US street lines almost always have at least one digit
        # (house number). If there's no digit at all, it's probably "Apt 4B"
        # or some other fragment.
        if not any(ch.isdigit() for ch in norm["line1"]):
            errors.append(
                f"shipping_address_line1 '{norm['line1']}' has no street "
                f"number — confirm the full address with the customer"
            )
        # Real street line has at least 2 tokens (number + street name).
        # Catches cases like "Apt 4B" or "4B" that scraped past the
        # length check but obviously aren't a full street line.
        elif len(norm["line1"].split()) < 2:
            errors.append(
                f"shipping_address_line1 '{norm['line1']}' looks incomplete — "
                f"need the full street like '123 Main St', not just a number"
            )

    # --- city ---
    if not norm["city"]:
        errors.append("shipping_city is empty")
    elif len(norm["city"]) < 2:
        errors.append(f"shipping_city '{norm['city']}' is too short")
    else:
        # Normalize "san francisco" → "San Francisco" while preserving
        # legitimate capitalization like "McLean" — only title-case if input
        # is all lowercase or all uppercase.
        if norm["city"].islower() or norm["city"].isupper():
            norm["city"] = norm["city"].title()

    # --- state ---
    if not norm["state"]:
        errors.append("shipping_state is empty")
    else:
        canonical = _normalize_state(norm["state"])
        if canonical is None:
            errors.append(
                f"shipping_state '{norm['state']}' is not a valid US state code. "
                f"Use the 2-letter code (CA, NY, TX, etc.) or full name."
            )
        else:
            norm["state"] = canonical

    # --- zip ---
    zip_canonical, zip_err = _normalize_zip(norm["zip"])
    if zip_err:
        errors.append(zip_err)
    else:
        norm["zip"] = zip_canonical

    # --- country ---
    country_canonical, country_err = _normalize_country(norm["country"])
    if country_err:
        errors.append(country_err)
    else:
        norm["country"] = country_canonical

    # --- Cross-field: state vs ZIP first-digit sanity ---
    # Each US state has a known set of ZIP prefixes (first digit). Catches
    # cases where the LLM mixed up state + ZIP (e.g. CA + 10001).
    if not errors:  # only check if both state and zip parsed individually
        ok, msg = _state_zip_consistent(norm["state"], norm["zip"])
        if not ok:
            errors.append(msg)

    return (len(errors) == 0), norm, errors


# US ZIP-code first-digit → set of valid states. Built from the USPS
# documented ZIP regions. Catches gross mismatches like "CA, 10001".
# (Not comprehensive — e.g. ZIP 0 spans MA, NH, VT, ME, CT, RI, PR, VI, AE
# military addresses — but good enough to catch the obvious LLM mix-ups.)
_ZIP_PREFIX_STATES = {
    "0": {"MA", "ME", "NH", "VT", "CT", "RI", "NJ", "PR", "VI", "AE"},
    "1": {"DE", "NY", "PA"},
    "2": {"DC", "MD", "NC", "SC", "VA", "WV"},
    "3": {"AL", "FL", "GA", "MS", "TN", "AA", "AE"},
    "4": {"IN", "KY", "MI", "OH"},
    "5": {"IA", "MN", "MT", "ND", "SD", "WI"},
    "6": {"IL", "KS", "MO", "NE"},
    "7": {"AR", "LA", "OK", "TX"},
    "8": {"AZ", "CO", "ID", "NM", "NV", "UT", "WY"},
    "9": {"AK", "AS", "CA", "GU", "HI", "MP", "OR", "WA", "AP"},
}


def _state_zip_consistent(state: str, zip_code: str) -> tuple[bool, str]:
    """Light sanity check: does the ZIP's first digit match the state's region?

    Returns (ok, error_msg_if_not). Conservative — only flags clear mismatches.
    """
    if not state or not zip_code:
        return True, ""  # field-level errors handled above
    first = zip_code[0]
    valid_states = _ZIP_PREFIX_STATES.get(first, set())
    if state in valid_states:
        return True, ""
    return False, (
        f"shipping_state '{state}' and shipping_zip '{zip_code}' don't match — "
        f"ZIPs starting with '{first}' are typically in {sorted(valid_states)}. "
        f"Confirm with the customer whether the state or ZIP is wrong."
    )


# ---------------------------------------------------------------------------
# Convenience wrapper used by create_nash_order
# ---------------------------------------------------------------------------

def validate_order_inputs(email: str, address: dict) -> tuple[bool, dict, list[str]]:
    """Validate email + address together. Returns (ok, normalized, errors).

    normalized has keys: email, line1, line2, city, state, zip, country.
    """
    errors: list[str] = []

    email_ok, email_norm, email_err = validate_email(email)
    if not email_ok:
        errors.append(email_err or "email failed validation")

    # Route to the US validator (strict state/ZIP) or the international one
    # based on the resolved country.
    country, _ = _normalize_country(address.get("country") or "")
    if country == "US":
        addr_ok, addr_norm, addr_errs = validate_us_address(address)
    else:
        addr_ok, addr_norm, addr_errs = validate_intl_address(address)
    errors.extend(addr_errs)

    normalized = {"email": email_norm, **addr_norm}
    return (len(errors) == 0), normalized, errors
