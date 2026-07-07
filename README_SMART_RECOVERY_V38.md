# DFP 2.0 Backend v38 — Full Carrier-Phrase Rename Recovery

This version extends v37 smart recovery with the full rename/carrier-phrase pipeline.

## What changed from v37

- Added carrier-phrase rename detection after normal staged discovery fails.
- Rename carrier query runs as an extra budgeted Serper search:
  - `"<registered name>" registered OR society OR trust OR foundation`
- The scanner reads Knowledge Graph, organic, and places result title/snippet text, including listing/news/social pages as evidence carriers only.
- It detects phrases such as:
  - `registered as`
  - `formerly known as`
  - `now known as`
  - `rebranded as`
  - `became`
  - `merged with`
  - `acquired by`
  - `to become`
- If a carrier phrase links the uploaded registered name to a public brand, it extracts the public brand.
- If the carrier exposes an official-looking URL, that URL is verified directly.
- Otherwise it runs a targeted brand search:
  - `"<extracted brand>" official website`
- Target candidates are scored using the extracted brand, but final verification is against the original uploaded record.

## Safety rule

Carrier evidence never accepts a website by itself.

For rename-detected matches, the target website must verify the original record at Grade A or B:

- Grade A: a government/registration identifier from the record is found on the target site.
- Grade B: the original registered legal name is found on the target site.

Grade C is deliberately downgraded for rename route and becomes `needs_manual_verification`.

## Output

Accepted rename matches use:

- `Website Status = rename_verified_match`
- `Match Route = rename_detected`
- `Confidence = medium`

Audit rows include:

- `Carrier URL`
- `Carrier Phrase`
- `Match Route = rename_detected`
- accepted / nominated_not_verified decision

## Classic behavior

Classic `/repository/recheck/start` remains default. Smart behavior runs only with:

`POST /repository/recheck/start?strategy=smart`
