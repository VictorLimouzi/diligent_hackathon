# Robustness findings

Dev-data run, seed **0**, 2026-11-21. The unmodified engine returned **819 hits** from **440 alerts**: 463 `DIFFERENT_ENTITY`, 125 `SAME_ENTITY`, 231 `REVIEW`. The original practice labels still score **50/50**, including accepted reasons, customer records, and exact evidence. The existing golden output SHA-256 test passes.

The sweep found **7 new clears under meaning-preserving changes**, **4 new confirms**, and **1 additional new clear under deliberately malformed data**. Deleting `hit_ids` also silently drops one result. There were **no escaped exceptions or caught per-alert exceptions**. These are changes relative to the original decisions, not newly established ground-truth labels.

| Experiment | Changed decisions | New clears | New confirms | Clear → review | Exceptions | Missing results | Must-review violations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `names_variants` | 16 | 4 | 0 | 5 | 0 | 0 | 0 |
| `whitespace_nbsp` | 18 | 3 | 4 | 3 | 0 | 0 | 0 |
| `crash_missing_hit_ids` | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `crash_invalid_dates` | 1 | 1 | 0 | 0 | 0 | 0 | 1 |
| `crash_null_names` | 1 | 0 | 0 | 0 | 0 | 0 | 0 |

Other experiments produced no changed decisions. The missing result and the invalid-date clear each appear in two report categories; these are **13 distinct adverse hit/experiment outcomes**, rather than 15 separate defects. Every one has a verified reproduction changing **one field in one record** while keeping the rest of the complete dev folder intact.

## Reproduce any individual finding

From the repository root:

```sh
export CASEBOOK_DATA=data/screening/dev
python3 -m evals.run "$CASEBOOK_DATA" --seed 0

# One field in one hit, with an independent report:
python3 -m evals.run "$CASEBOOK_DATA" \
  --replay evals/reproductions/whitespace_nbsp-new_clears-H-A9AA55.json \
  --report evals/replay-report.json
```

Exit **1** is expected for a new clear, an exception, missing/unexpected output, or a malformed affected hit that does not become `REVIEW`. A new confirm alone is reported but does not fail the command, following the brief. Each replay file below gives the exact filename, original zero-based row index, record ID, field, original value, and replacement/deletion. Replays reject a mismatched original record or value. [reproductions.json](reproductions.json) also includes every full original record and the verified before/after decision and reason. [diagnostics.json](diagnostics.json) contains the corresponding engine traces.

## Listed variants combined with fuzzy names: four new clears

Every replacement below comes from the supplied name-variant groups. Each single-field reproduction gives the same outcome as the full sweep.

| Hit / alert | One record and one field change | Before → after | Replay |
| --- | --- | --- | --- |
| H-5819E5 / A-62128B | `hits.jsonl`, H-5819E5, `name`: one part replaced by a listed variant | `REVIEW / INSUFFICIENT_DATA` → `DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED` | [JSON](reproductions/names_variants-new_clears-H-5819E5.json) |
| H-4C3C21 / A-63A4D1 | `hits.jsonl`, H-4C3C21, `name`: one part replaced by a listed variant | `REVIEW / INSUFFICIENT_DATA` → `DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED` | [JSON](reproductions/names_variants-new_clears-H-4C3C21.json) |
| H-9F5114 / A-958B8F | `kyc_checks.jsonl`, KYC-D03F0B, `verified_name`: one part replaced by a listed variant | `SAME_ENTITY / NAME_LOCATION_MATCH` → `DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED` | [JSON](reproductions/names_variants-new_clears-H-9F5114.json) |
| H-404FB7 / A-963095 | `hits.jsonl`, H-404FB7, `name`: one part replaced by a listed variant | `REVIEW / INSUFFICIENT_DATA` → `DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED` | [JSON](reproductions/names_variants-new_clears-H-404FB7.json) |

**Source cause:** [casebook/names.py](../casebook/names.py), `parts_match` and `names_agree`, check direct variant membership and then edit distance on the actual spellings. They do not compare variant alternatives when one side is already a typo. The verified names of the first, second and fourth cases match the original hit spellings under the edit allowance; replacing the hit spelling with a listed variant exceeds that allowance. For the company, the hit is `Nikolas, LLC`: `Nikola` matches by one edit, while `nicola` requires two and the shorter six-letter part permits only one. Both company suffixes are discarded. [casebook/policy.py](../casebook/policy.py), `_name_check` and `decide`, turn the resulting verified-name disagreement directly into a clear.

## Non-breaking space in a category: three new clears and one new confirm

Here `\u00a0` denotes one U+00A0 non-breaking space. Replace **only** the space between `Adverse` and `Media`; no names, dates, snippets, or customer records need to change.

| Hit / alert | One record and field | Before → after | Replay |
| --- | --- | --- | --- |
| H-A9AA55 / A-03A381 | H-A9AA55, `primary_category`: `Adverse Media` → `Adverse\u00a0Media` | `SAME_ENTITY / NAME_DOB_LOCATION_MATCH` → `DIFFERENT_ENTITY / DOB_MISMATCH` | [JSON](reproductions/whitespace_nbsp-new_clears-H-A9AA55.json) |
| H-F9DBF9 / A-9BD747 | H-F9DBF9, `primary_category`: same replacement | `SAME_ENTITY / NAME_DOB_LOCATION_MATCH` → `DIFFERENT_ENTITY / DOB_MISMATCH` | [JSON](reproductions/whitespace_nbsp-new_clears-H-F9DBF9.json) |
| H-29397A / A-ED1C9A | H-29397A, `categories`: same replacement | `REVIEW / INSUFFICIENT_DATA` → `DIFFERENT_ENTITY / DOB_MISMATCH` | [JSON](reproductions/whitespace_nbsp-new_clears-H-29397A.json) |
| H-9FBBCA / A-F05DFC | H-9FBBCA, `categories`: same replacement | `REVIEW / INSUFFICIENT_DATA` → `SAME_ENTITY / NAME_DOB_LOCATION_MATCH` | [JSON](reproductions/whitespace_nbsp-new_confirms-H-9FBBCA.json) |

All four records are in `hits.jsonl`.

**Source cause:** [casebook/extract.py](../casebook/extract.py), `listing_phrase`, strips the category's edges and lowercases it but does not normalize internal whitespace. `Adverse\u00a0Media` misses the literal `adverse media` dictionary key and becomes the fallback **“named on a watchlist”** category. [casebook/hit.py](../casebook/hit.py), `gather`, then accepts search results with that different listing phrase and uses their ages as the hit's DOB evidence.

For hits H-A9AA55 and H-F9DBF9 (the same listed person), the accepted source changes from SR-AE2CE1, birth years **1959/1960**, to SR-8D8EC2, **1988/1989**. The verified customer DOB remains **1959-09-12**, so `_dob_check` clears the hits. For H-29397A, no DOB becomes **1971/1972** from SR-C982A1, contradicting the customer's **1997-12-06**. For H-9FBBCA, the same newly accepted source instead agrees with the customer's **1972-01-22** and confirms the hit. This is wrong-source selection, rather than simply losing an unreadable fact.

## Non-breaking spaces in report text: three new confirms

For each row below, change only that hit's `profile_notes` field in `hits.jsonl` with `old_value.replace(" ", "\u00a0")`. Preserve its capitalization, punctuation, paragraph boundaries, and dates. The exact full field values are in the replay JSON.

| Hit / alert | Report date lost | Before → after | Replay |
| --- | --- | --- | --- |
| H-8DAC34 / A-0C64C7 | 2013-04-01 | `DIFFERENT_ENTITY / TEMPORAL_IMPOSSIBILITY` → `SAME_ENTITY / NAME_LOCATION_MATCH` | [JSON](reproductions/whitespace_nbsp-new_confirms-H-8DAC34.json) |
| H-19AC91 / A-0F6850 | 1998-03-30 | `DIFFERENT_ENTITY / TEMPORAL_IMPOSSIBILITY` → `SAME_ENTITY / NAME_LOCATION_MATCH` | [JSON](reproductions/whitespace_nbsp-new_confirms-H-19AC91.json) |
| H-A3B158 / A-98AA13 | 2007-11-16 | `DIFFERENT_ENTITY / TEMPORAL_IMPOSSIBILITY` → `SAME_ENTITY / NAME_LOCATION_MATCH` | [JSON](reproductions/whitespace_nbsp-new_confirms-H-A3B158.json) |

**Source cause:** [casebook/extract.py](../casebook/extract.py), `_REPORT` / `dated_reports`, requires literal ordinary spaces in `^According to `. A typographically equivalent prefix containing U+00A0 no longer matches. `gather` loses the earliest report; [casebook/policy.py](../casebook/policy.py), `_time_check`, consequently returns unknown. Company incorporation dates remain **2014**, **2012-11-14**, and **2008**, respectively. With the temporal contradiction removed, the remaining company name and country agreements are enough for `decide` to confirm.

## Malformed data: a new clear and a silent omission

These probes intentionally destroy information; they are **not** counted among the seven meaning-preserving new clears.

| Hit / alert | One record and one field change | Before → after | Replay |
| --- | --- | --- | --- |
| H-9AD76C / A-834045 | `hits.jsonl`, H-9AD76C, `dobs`: `1995; 1994` → `0000-00-00` | `REVIEW / AMBIGUOUS_CUSTOMER_RECORD` → `DIFFERENT_ENTITY / LOCATION_MISMATCH` | [JSON](reproductions/crash_invalid_dates-new_clears-H-9AD76C.json) |
| H-57FFA7 / A-94B5CD | `alerts.jsonl`, A-94B5CD: delete `hit_ids`, whose original value is `H-57FFA7` | `DIFFERENT_ENTITY / NAME_MISMATCH_VERIFIED` → **no result** | [JSON](reproductions/crash_missing_hit_ids-missing_results-H-57FFA7.json) |

**Invalid DOB cause:** [casebook/dates.py](../casebook/dates.py), `_valid` / `parse_date`, discards the invalid date. `gather` supplies no hit DOB. [casebook/policy.py](../casebook/policy.py), `decide`, suppresses a location clear only when **both name and DOB agree**. Losing DOB removes that safeguard for this customer candidate. The Rachel Martinez candidate already clears on name; now both candidates clear, and [casebook/engine.py](../casebook/engine.py), `decide_alert`, settles the ambiguous customer as `DIFFERENT_ENTITY`. The minimal reproduction leaves the alert's dates valid; the malformed hit field alone is sufficient.

**Missing-hit-list cause:** `decide_alert` iterates `as_list(alert.get("hit_ids"))`. [casebook/load.py](../casebook/load.py), `as_list`, maps a missing key to an empty list, so no result is emitted. No exception triggers `decide_all`'s fallback, whose fallback loop would also use that same missing list. The hit still exists in `hits.jsonl` with its `alert_id`, but the engine does not recover it from that relationship. The harness treats disappearance as a failure rather than a successful reduction in changes.

For seed 0 the unknown-hit, null-name, and missing-account probes satisfy the affected-hit `REVIEW` requirement. Only one alert per malformed probe is selected, deterministically; these results do not claim exhaustive malformed-data coverage.

## Stable experiments and scope

No decision changes were found for the four date formats; customer country codes/nationalities; case, accent removal/addition, hyphens, and token ordering; JSON lists, numeric scores/counts, optional missing/null/empty fields; key/row order; filename forms with the supplied or equivalent fallback rules; edge whitespace; curly apostrophes; NFC/NFD; or note/snippet rewrapping. The run exercises the complete dev folder, not a label-only subset. Date perturbations preserve full-date precision and leave partial dates and timestamps intact. Category and report typography tests preserve month-name capitalization. Identifiers and URLs are excluded from typography edits.

See [report.json](report.json) for counts, up to 20 examples per category, every adverse event without truncation, and source hashes. Its deterministic compressed companion, [report.records.json.gz](report.records.json.gz), identifies every touched record by experiment and source filename. No engine change was made.
