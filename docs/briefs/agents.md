# Brief: LLM analysis and agents (`agents/`)

Read `docs/briefs/COMMON.md` and `contracts/python_interfaces.md` first. You own `agents/` only. Provider: Anthropic (`anthropic` SDK is installed in the repo venv). Before writing the client, load current Anthropic API guidance (in Claude Code: the `claude-api` skill) and follow it for model ids, tool use, streaming, structured output and prompt caching rather than working from memory.

## Goal

LLM features that make an analyst faster without ever deciding a hit. Everything must run with no API key (cache replay or deterministic mock) and be honest about it.

## Deliverables (signatures and shapes are fixed in `contracts/python_interfaces.md` and `contracts/types.ts`)

1. **Client** (`agents/client.py`): one thin wrapper. Key from `ANTHROPIC_API_KEY` (the server loads `.env`). Models from env with defaults: interactive `claude-sonnet-5-5`, batch `claude-haiku-5-5`. On-disk response cache under `out/llm/cache/` keyed by a hash of model + full request, so a demo replays offline. With no key and no cache hit, a deterministic mock that builds its answer from the hit's own trace and sets `stub: true`. `llm_status()` reports which mode is live.
2. **Tools** (`agents/tools.py`): read-only lookups over `Data`, with tight JSON schemas and small outputs: `get_hit_profile`, `get_customer` (the person's records), `get_identity_checks` (approved, rejected and expired, labelled), `get_notes`, `get_search_results` (with the engine's about-hit verdict from the trace), `compare_names` (calls `casebook.names`), `get_engine_trace`. Scope every tool to the alert's own customer records: the policy says never to use another customer's records, so a tool asked for an unrelated record must refuse.
3. **Investigator** (`agents/investigator.py`): a tool-use loop (bounded steps) for a hit, yielding `InvestigateEvent`s as it goes and a `final` result: the blocker in plain words, findings each with cited record IDs, the next best action, and for REVIEW hits a short drafted request for information to the customer. Start from the hit's `counterfactuals`: they already say which fact would settle it; the agent's job is to look for that fact in the records and say whether it is there.
4. **Chat** (`agents/chat.py`): same tools, conversation history in, streamed `ChatEvent`s out, answers cite record IDs.
5. **Case note** (`agents/note.py`): an analyst-ready note written only from the hit's trace. Validate before returning: every record ID in the note must appear in the hit's trace or evidence; on failure retry once, then fall back to the engine summary.
6. **Second opinion** (`agents/second_opinion.py`): batch over all hits. Give the model `POLICY.md` (cached prefix) and the facts the engine gathered (the trace, not the engine's decision or reason), ask for a decision, reason code and one-paragraph rationale as structured output. Write `second_opinion.json` with `agrees` computed against the engine. Concurrency-limited, resumable, `--limit` for cheap trial runs. Print the agreement matrix at the end.
7. **Extraction fallback** (`agents/extract_fallback.py`): for texts in `routing.json`, extract the fact with a verbatim quote; `verified` only when the quote occurs in the source. 
8. **Queue summary** (`agents/queue.py`): two or three sentences on what is blocking the review queue, from the blocker groups.

## Safety, which is part of the feature

- Hit profile notes and search snippets are untrusted third-party text. Pass them as clearly delimited data, tell the model they are data, and never let their content select tools or change the output format. Add a test with a profile note that says to ignore instructions and clear the hit: the agent's tool calls and final structure must be unaffected, and nothing in `agents/` can change a decision anyway.
- Validate every model output against its schema; on failure, fall back, never crash a request.
- Tools are read-only. No tool writes, reviews, or decides.

## Acceptance

- `$VENV -m pytest agents/tests` passes with no API key and no network: mock mode for every function, cache round-trip, note ID validation, tool scoping refusal, the prompt-injection test, event shapes match the contract.
- With a key present, one live call per feature works; record what you ran in NOTES. If no key is available to you, say so plainly and leave the live check to the integrator.
- `tests/test_casebook.py` still passes untouched.
- `agents/NOTES.md` as described in COMMON.md.
