# Getting the Typeform sync running again

Your old machine ran a local script at 7am that pulled from Typeform and
pushed to this repo. That script is gone; this replaces it with a GitHub
Action, so it runs regardless of what machine is on.

## 1. Rotate your credentials (do this first, before anything else)

You pasted partial tokens in chat. Treat both as compromised:
- GitHub: Settings → Developer settings → Personal access tokens → revoke
  the old one, generate a fresh one (you may not even need this one, see
  step 3).
- Typeform: Account settings → Personal tokens → revoke the old one,
  generate a new one.

## 2. Add the two files in this bundle to your repo

- `scripts/sync_typeform.py`
- `.github/workflows/sync-typeform.yml`

Commit both to `main`.

## 3. Add the Typeform token as a repo secret

Repo → Settings → Secrets and variables → Actions → New repository secret

- Name: `TYPEFORM_TOKEN`
- Value: your new Typeform token

You do **not** need a GitHub personal access token for this. The workflow
uses the built-in `GITHUB_TOKEN` that Actions provides automatically,
scoped to just this repo, with write access already granted by the
`permissions: contents: write` line in the workflow file.

## 4. Sanity-check the field mapping before trusting a live run

The script matches Typeform questions to dashboard fields (CRM, ERP, phone,
satisfaction scores, frustration, opportunity) by keyword in the question
title, because the original field-ID mapping was lost with the old script.

Run this locally once to check it picked the right fields:

```bash
export TYPEFORM_TOKEN="your_new_token"
python scripts/sync_typeform.py --inspect
```

This prints every question title/id in your form and which one the script
matched to each dashboard field. If anything shows as unmapped, or matched
to the wrong question, open `scripts/sync_typeform.py` and adjust the
`FIELD_KEYWORDS` dict near the top, no need to touch anything else.

Also check `REGION_BY_DOMAIN_SUFFIX` near the top of the script. Region
is inferred from the respondent's email domain (`.nz` → NZ, `.au` → AU,
`.uk` → UK, `.us`/`.ca` → NA, everything else → FR/Europe catch-all).
That's a reasonable guess reconstructed from the current data, not
necessarily identical to whatever logic the original script used.

## 5. Trigger a manual run

Repo → Actions → "Sync Typeform responses" → Run workflow

Check the run log, then check `index.html` updated correctly and the
dashboard renders as expected.

## 6. From here it runs itself

Scheduled for 7am NZT daily. You can also trigger it manually any time
from the Actions tab if you don't want to wait for the schedule.
