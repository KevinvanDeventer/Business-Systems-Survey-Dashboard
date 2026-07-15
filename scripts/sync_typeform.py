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
"""

import os
import re
import sys
import json
import datetime
import urllib.request
import urllib.error

FORM_ID = os.environ.get("TYPEFORM_FORM_ID", "hxM2ihJz")
TOKEN = os.environ.get("TYPEFORM_TOKEN")
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "..", "index.html")

API_BASE = "https://api.typeform.com"

# ---------------------------------------------------------------------------
# Keyword matching: since the original field-ID mapping was lost, fields are
# matched by keywords found in their Typeform question title. If auto-match
# picks the wrong field, add/adjust keywords here rather than hunting for IDs.
# ---------------------------------------------------------------------------
FIELD_KEYWORDS = {
    "company":     ["company", "organisation", "organization", "business name"],
    "email":       ["email"],
    "crm":         ["crm"],
    "crm_sat":     ["crm", "satisf"],
    "erp":         ["erp"],
    "erp_sat":     ["erp", "satisf"],
    "phone":       ["phone", "voip", "telephony"],
    "phone_sat":   ["phone", "satisf"],
    "frustration": ["frustrat", "biggest challenge", "pain point"],
    "opportunity": ["opportunit", "improve", "biggest win"],
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


def classify_fields(fields):
    """Map each logical key (company, crm, crm_sat, ...) to a Typeform field id."""
    mapping = {}
    for key, keywords in FIELD_KEYWORDS.items():
        best = None
        for f in fields:
            title = f.get("title", "").lower()
            if all(kw in title for kw in keywords):
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
        sys.exit("Could not find `const RESPONSES = [ ... ];` block in index.html. Aborting.")
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


def main():
    inspect_only = "--inspect" in sys.argv

    fields = get_form_fields()
    field_map = classify_fields(fields)

    if inspect_only:
        print("Form fields (id, title):")
        for f in fields:
            print(f"  {f['id']:>20}  {f.get('title','')!r}")
        print("\nAuto-detected mapping:")
        for key, fid in field_map.items():
            print(f"  {key:>12} -> {fid}")
        return

    unmapped = [k for k, v in field_map.items() if v is None]
    if unmapped:
        print(f"WARNING: could not auto-match fields for: {unmapped}", file=sys.stderr)
        print("Run with --inspect to see all field titles and adjust FIELD_KEYWORDS.", file=sys.stderr)

    responses = get_all_responses()
    print(f"Fetched {len(responses)} responses from Typeform form {FORM_ID}")

    records = build_records(responses, field_map)
    js = records_to_js(records)
    rewrite_index_html(js)
    print(f"Wrote {len(records)} records into {INDEX_HTML_PATH}")


if __name__ == "__main__":
    main()
