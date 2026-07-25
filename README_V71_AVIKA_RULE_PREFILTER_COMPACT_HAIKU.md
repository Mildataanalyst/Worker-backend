# V71 — Avika Rule Prefilter + Compact Haiku Classification

## Objective

Use Haiku only for the judgment that requires a model. Obvious non-fits are rejected locally before Anthropic is called, and remaining NGOs receive a compact `yes / maybe / no` classification rather than a descriptive website profile.

## Fast Recovery flow

1. Recover and fetch the NGO website.
2. Apply conservative deterministic exclusion rules.
3. Send only unresolved/plausible rows to Haiku.
4. Haiku returns only:
   - official website match
   - Avika decision (`yes`, `maybe`, `no`)
   - confidence
   - reason code
5. Write the same filtered repository, audit, and rejected CSV outputs.

Rules can only reject. They never auto-approve or shortlist an NGO.

## High-confidence rule exclusions

- College, university, and professional-institute identities
- Hospital, clinic, and health-only identities
- Elderly-only organisations
- Adult livelihood, SHG, farmer, and women-only skilling organisations
- Religious/spiritual bodies without a child programme
- Community/caste/membership associations without a child programme
- Clearly premium/private fee-charging schools
- Directory, social-media, blog, news, listing, and other non-owned sources

Ambiguous cases and plausible child-focused organisations continue to Haiku.

## Fast Recovery defaults

The parent worker sets these automatically for the Fast Recovery Avika stage:

```env
AVIKA_RULE_PREFILTER=true
AVIKA_CLASSIFICATION_ONLY=true
AVIKA_SITE_TEXT_CHARS=1500
AVIKA_MAX_TOKENS=80
DFP_FILTER_VERSION=avika_fit_v3_rule_compact
```

They may be overridden in Railway. `1500` characters and `80` output tokens are the recommended balance between classification quality and cost.

## Cost controls

- Website evidence is reduced from up to 4,000 raw characters to up to 1,500 targeted characters.
- Output is reduced from a 13-field descriptive profile with up to 700 tokens to a 4-field compact classification with up to 80 tokens.
- Rule-rejected rows consume no Anthropic tokens.
- Haiku Batch API remains enabled for bulk filtering.

## Safety and resume

- All rule rejections are retained in the rejected/audit CSV with a reason code.
- Anthropic credit exhaustion still hard-pauses the run.
- Completed rule decisions and AI results remain checkpointed.
- Resume continues the same Avika run without repeating completed website recovery.
- The V71 filter signature invalidates incompatible V70 Avika checkpoints for the same input and starts the new compact filter cleanly.

## Compatibility

Normal Bulk Discovery retains the existing rich profile unless the `AVIKA_CLASSIFICATION_ONLY` variable is explicitly enabled. Fast Recovery enables compact classification automatically.

## Validation

- Python compilation passed.
- Full worker test suite: 53 passed.
