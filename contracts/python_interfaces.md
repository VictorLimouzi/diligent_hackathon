# Python interfaces between streams

Frozen. The server (`server/advisory.py`) calls exactly these; it falls back to stand-ins while a package is missing.

Shared inputs:

- `data`: `casebook.load.Data(folder)`. Read-only lookups: `data.customers[record_id]`, `data.hits[hit_id]`, `data.alerts`, `data.by_id[id]` (CUS/KYC/NOTE/SR rows), `data.acc_by_cust`, `data.onb_by_cust`, `data.kyc_by_app`, `data.notes_by_email`, `data.search_results(name)`, `data.person(record_id)`, `data.parts(name, company)`, `data.rules`.
- `alert`: the alert's fields as a dict of strings.
- `hit`: one `Hit` object exactly as in `contracts/types.ts` (the engine's decision, trace, counterfactuals, profile).
- Engine results with feature rows: `casebook.engine.decide_all(data)` returns one dict per hit; `result["features"]` is the flat row from `casebook/features.py::feature_row`.

## `ml/` (scikit-learn)

```
python -m ml.train <data_folder> --out out/ml
```

writes

- `out/ml/scores.json`: `{hit_id: MlScores}` for every hit (shape in `contracts/types.ts`)
- `out/ml/metrics.json`: free-form dict of validation numbers; include `ranker_masked_auc` and `name_model_accuracy`
- `out/ml/routing.json`: `{"notes": [{id, text, predicted, reason}], "snippets": [...]}`, the texts the router says carry a fact the regex extractors did not read

```python
from ml.search import CustomerSearch
CustomerSearch(data).query(q: str, k: int = 10) -> list[{"record_id", "full_name", "dob", "country", "score"}]
```

## `agents/` (Anthropic SDK)

```python
from agents import llm_status, write_note, investigate, chat, queue_summary

llm_status() -> {"live": bool, "model_interactive": str, "model_batch": str}
write_note(hit) -> {"note": str, "cited_ids": [str], "cached": bool, "model": str}
investigate(data, alert, hit) -> Iterator[InvestigateEvent]   # ends with one "final" event
chat(data, alert, hit, messages) -> Iterator[ChatEvent]       # ends with one "done" event
queue_summary(groups) -> {"text": str, "cached": bool}        # groups: [{blocker, count, hit_ids}]
```

```
python -m agents.second_opinion <data_folder> --out out/llm/second_opinion.json [--limit N]
python -m agents.extract_fallback <data_folder> --routing out/ml/routing.json --out out/llm/extractions.json
```

- `second_opinion.json`: `{hit_id: SecondOpinion}`
- `extractions.json`: `{text_id: {"fact": {...}, "quote": str, "verified": bool}}`; `verified` is true only when `quote` occurs in the source text

Every function must work with no API key by replaying the on-disk cache or a deterministic mock, and must say so (`"cached": true` or `"stub": true`).
