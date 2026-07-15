#!/usr/bin/env python3
"""
Pulls responses from a Typeform form and rewrites the RESPONSES array
embedded in index.html for the Business Systems Survey Dashboard.

Required environment variables:
  TYPEFORM_TOKEN   Typeform personal access token (repo secret, never committed)
  TYPEFORM_FORM_ID Typeform form ID (safe to hardcode below, it's not sensitive)

Usage:
  python scripts/sync_typeform.py            # normal run, rewrites index.html
  python scripts/sync_typeform.py --inspect  # just print field titles/ids, no write
  python scripts/sync_typeform.py --raw      # print raw fields + first response, no write
"""

import os
import re
import sys
import json
import datetime
import urllib.request
import urllib.error

SCRIPT_VERSION = "v3-group-fix"

FORM_ID = os.environ.get("TYPEFORM_FORM_ID", "hxM2ihJz")
TOKEN = os.environ.get("TYPEFORM_TOKEN")
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")
API_BASE = "https://api.typeform.com"

# ---------------------------------------------------------------------------
# Keyword matching against FLATTENED fields (Typeform nests real questions
# inside "group" fields via properties.fields — the old version only looked
# at the top-level fields list, which is why every mapping came back None).
# ---------------------------------------------------------------------------
CRM_WORDS = ["crm"]
ERP_WORDS = ["erp"]
PHONE_WORDS = ["phone", "voip", "telephony"]
SAT_WORDS = ["satisf"]
COMPANY_WORDS = ["company", "organisation", "organization", "business name"]
FRUSTRATION_WORDS = ["frustrat", "biggest challenge", "pain point"]
OPPORTUNITY_WORDS = ["opportunit", "improve", "biggest win"]


def _any_word_in(title, words):
    return any(w in title for w in words)


FIELD_RULES = {
    "company":     lambda t: _any_word_in(t, COMPANY_WORDS),
    "email":       lambda t: "email" in t,
    "crm":         lambda t: _any_word_in(t, CRM_WORDS) and not _any_word_in(t, SAT_WORDS),
    "crm_sat":     lambda t: _any_word_in(t, CRM_WORDS) and _any_word_in(t, SAT_WORDS),
    "erp":         lambda t: _any_word_in(t, ERP_WORDS) and not _any_word_in(t, SAT_WORDS),
    "erp_sat":     lambda t: _any_word_in(t, ERP_WORDS) and _any_word_in(t, SAT_WORDS),
    "phone":       lambda t: _any_word_in(t, PHONE_WORDS) and not _any_word_in(t, SAT_WORDS),
    "phone_sat":   lambda t: _any_word_in(t, PHONE_WORDS) and _any_word_in(t, SAT_WORDS),
    "frustration": lambda t: _any_word_in(t, FRUSTRATION_WORDS),
    "opportunity": lambda t: _any_word_in(t, OPPORTUNITY_WORDS),
}

# Domain suffix -> region bucket, matching the four+catch-all regions
# already used in the dashboard (NZ, AU, UK, NA, FR=Europe catch-all).
REGION_BY_DOMAIN_SUFFIX = [
    (".nz", "NZ"),
    (".au", "AU"),
    (".uk", "UK"),
    (".us", "NA"),
    (".ca", "NA"),
]
DEFAULT_REGION = "FR"  # catch-all bucket for continental Europe / unmatched


def api_get(path, params=None):
    if not TOKEN:
        sys.exit("TYPEFORM_TOKEN is not set. Aborting, refusing to run without auth.")
    url = f"{API_BASE}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.exit(f"Typeform API error {e.code} on {url}:\n{body}")


def get_form_fields():
    form = api_get(f"/forms/{FORM_ID}")
    return form.get("fields", [])


def flatten_fields(fields):
    """Recursively expand group/matrix fields so nested questions are visible."""
    flat = []
    for f in fields:
        nested = f.get("properties", {}).get("fields")
        if nested:
            flat.extend(flatten_fields(nested))
        else:
            flat.append(f)
    return flat


def classify_fields(fields):
    """Map each logical key (company, crm, crm_sat, ...) to a Typeform field id."""
    flat = flatten_fields(fields)
    mapping = {}
    for key, rule in FIELD_RULES.items():
        best = None
        for f in flat:
            title = f.get("title", "").lower()
            if rule(title):
                best = f["id"]
                break
        mapping[key] = best
    return mapping


def get_all_responses():
    responses = []
    params = {"page_size": 1000}
    while True:
        page = api_get(f"/forms/{FORM_ID}/responses", params)
        items = page.get("items", [])
        responses.extend(items)
        if len(items) < 1000:
            break
        last_token = items[-1].get("token")
        params["before"] = last_token
    return responses


def answer_value(answer):
    if answer is None:
        return None
    t = answer.get("type")
    if t == "text" or t == "email":
        return answer.get(t)
    if t == "choice":
        return answer.get("choice", {}).get("label")
    if t == "number":
        return answer.get("number")
    if t == "opinion_scale" or t == "rating":
        return answer.get(t)
    return None


def infer_region(email):
    if not email or "@" not in email:
        return DEFAULT_REGION
    domain = email.split("@")[-1].lower()
    for suffix, region in REGION_BY_DOMAIN_SUFFIX:
        if domain.endswith(suffix):
            return region
    return DEFAULT_REGION


def js_string(s):
    """Escape a Python string for embedding in a JS single-quoted-free literal."""
    if s is None:
        return '""'
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return f'"{s}"'


def js_number_or_null(v):
    if v is None:
        return "null"
    try:
        return str(int(round(float(v))))
    except (ValueError, TypeError):
        return "null"


def build_records(responses, field_map):
    records = []
    for r in responses:
        answers_by_id = {a["field"]["id"]: a for a in r.get("answers", [])}

        def val(key):
            fid = field_map.get(key)
            if not fid:
                return None
            return answer_value(answers_by_id.get(fid))

        email = val("email")
        record = {
            "company": val("company") or "Unknown",
            "region": infer_region(email),
            "crm": val("crm") or "",
            "crmSat": val("crm_sat"),
            "erp": val("erp") or "",
            "erpSat": val("erp_sat"),
            "phone": val("phone") or "",
            "phoneSat": val("phone_sat"),
            "frustration": val("frustration") or "",
            "opportunity": val("opportunity") or "",
        }
        records.append(record)
    return records


def records_to_js(records):
    lines = ["const RESPONSES = ["]
    for rec in records:
        parts = [
            f'company:{js_string(rec["company"])}',
            f'region:"{rec["region"]}"',
            f'crm:{js_string(rec["crm"])}',
            f'crmSat:{js_number_or_null(rec["crmSat"])}',
            f'erp:{js_string(rec["erp"])}',
            f'erpSat:{js_number_or_null(rec["erpSat"])}',
            f'phone:{js_string(rec["phone"])}',
            f'phoneSat:{js_number_or_null(rec["phoneSat"])}',
            f'frustration:{js_string(rec["frustration"])}',
            f'opportunity:{js_string(rec["opportunity"])}',
        ]
        lines.append("  {" + ",".join(parts) + "},")
    lines.append("];")
    return "\n".join(lines)


def rewrite_index_html(new_array_js):
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    pattern = re.compile(r"const RESPONSES = \[.*?\n\];", re.DOTALL)
    if not pattern.search(html):
        sys.exit("Could not find const RESPONSES = [ ... ]; block in index.html. Aborting.")
    html = pattern.sub(new_array_js, html, count=1)
    month_year = datetime.datetime.utcnow().strftime("%B %Y")
    html = re.sub(
        r"Generated \w+ \d{4}",
        f"Generated {month_year}",
        html,
        count=1,
    )
    with open(INDEX_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)


def print_fields_recursive(fields, indent=0):
    for f in fields:
        pad = "  " * indent
        print(f"{pad}{f.get('type'):>10}  {f['id']:>20}  {f.get('title','')!r}")
        nested = f.get("properties", {}).get("fields")
        if nested:
            print_fields_recursive(nested, indent + 1)


def main():
    print(f"sync_typeform.py {SCRIPT_VERSION}")
    inspect_only = "--inspect" in sys.argv
    raw_mode = "--raw" in sys.argv

    fields = get_form_fields()
    field_map = classify_fields(fields)

    if raw_mode:
        print("=== FORM FIELDS (recursive, shows nested group/matrix fields) ===")
        print_fields_recursive(fields)
        print("\n=== RAW ANSWERS FROM FIRST RESPONSE ===")
        page = api_get(f"/forms/{FORM_ID}/responses", {"page_size": 1})
        items = page.get("items", [])
        if items:
            print(json.dumps(items[0].get("answers", []), indent=2))
        else:
            print("No responses returned.")
        return

    if inspect_only:
        print("Form fields (id, title):")
        for f in flatten_fields(fields):
            print(f"  {f['id']:>20}  {f.get('title','')!r}")
        print("\nAuto-detected mapping:")
        for key, fid in field_map.items():
            print(f"  {key:>12} -> {fid}")
        return

    unmapped = [k for k, v in field_map.items() if v is None]
    if unmapped:
        print(f"WARNING: could not auto-match fields for: {unmapped}", file=sys.stderr)
        print("Run with --inspect to see all field titles and adjust FIELD_RULES.", file=sys.stderr)

    responses = get_all_responses()
    print(f"Fetched {len(responses)} responses from Typeform form {FORM_ID}")
    records = build_records(responses, field_map)
    js = records_to_js(records)
    rewrite_index_html(js)
    print(f"Wrote {len(records)} records into {INDEX_HTML_PATH}")


if __name__ == "__main__":
    main()
