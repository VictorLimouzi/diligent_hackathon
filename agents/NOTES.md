# agents/ notes

LLM analysis and agent features for Casebook, on the Anthropic API. Advisory only: nothing here produces or alters an engine decision.

**The live paths have not been run.** There was no API key in the build environment, so every call to the real API is unverified. Everything was tested offline: the deterministic mock, cache replay, a scripted model, and the real `anthropic` client with its HTTP layer replaced in-process. The list of what remains to check live, and the one command that checks it, is under "Live check" below.

## What is here

| File | Job |
|---|---|
| `__init__.py` | Exports exactly `llm_status, write_note, investigate, chat, queue_summary` |
| `client.py` | The only code that calls the API. Config from env, disk cache, streaming call, error mapping, `llm_status()` |
| `tools.py` | `ToolBox(data, alert, hit)`: seven read-only lookups scoped to the alert's own customer and hit, plus their JSON schemas |
| `loop.py` | The bounded tool-use loop shared by the investigator and the chat |
| `investigator.py` | `investigate()`: looks for the fact the hit's counterfactuals name; ends with one `final` event |
| `chat.py` | `chat()`: history in, streamed `ChatEvent`s out; ends with one `done` event |
| `note.py` | `write_note()`: note from the hit's trace, record IDs validated, one retry, then the engine summary |
| `queue.py` | `queue_summary()`: two or three sentences from the blocker groups |
| `second_opinion.py` | Batch command: independent decision per hit, written next to the engine's |
| `extract_fallback.py` | Batch command: facts with a verbatim quote for the texts in `routing.json` |
| `live_check.py` | One call per feature with a verdict each; the live acceptance check |
| `mock.py` | The deterministic stand-ins used when no model answers |
| `prompts.py`, `schemas.py` | Fixed system prompts; output schemas, a small schema validator, record-ID helpers |
| `tests/` | 119 tests, no key and no network needed |

## Three ways a request is answered

1. **Cache.** Every response is stored under `out/llm/cache/<sha256>.json`, keyed by a hash of the whole request (model, system, tools, messages, output settings). A cached run is marked `cached: true`. A demo that was run once with a key replays offline, tool loop included, because each turn's request is rebuilt identically.
2. **Live**, when `ANTHROPIC_API_KEY` is set.
3. **Mock**, otherwise, marked `stub: true`. The mock is not a placeholder: the investigator plans its lookups from the hit's counterfactuals and runs them through the same tools, so the timeline shows real records; findings come from the engine's checks and from what the lookups returned (for example "NOTE-… mentions Canada, but not as a place the customer lived"); the request for information is a neutral template.

A response that fails validation is deleted from the cache so that a later run asks again. Refusals and cut-off replies are never cached.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | The key. `ANTHROPIC_AUTH_TOKEN` also counts. A profile from `ant auth login` does not make `llm_status()` live |
| `CASEBOOK_LLM_MODEL` | `claude-sonnet-5-5` | Interactive model: investigator, chat, note, queue summary |
| `CASEBOOK_LLM_BATCH_MODEL` | `claude-haiku-5-5` | Batch model: second opinion, extraction |
| `CASEBOOK_LLM_CACHE` | `$CASEBOOK_OUT/llm/cache`, else `<repo>/out/llm/cache` | Cache folder |
| `CASEBOOK_LLM_MOCK=1` | off | Never call the API, even with a key |
| `CASEBOOK_LLM_REFRESH=1` | off | Ignore cached responses (new ones are still written) |
| `CASEBOOK_LLM_FALLBACK=0` | on | Do not ask for server-side refusal fallback |
| `CASEBOOK_LLM_TIMEOUT`, `CASEBOOK_LLM_MAX_RETRIES` | 120 s, 3 | Per request |

The server loads `<repo>/.env`; the three commands below do too.

## Run and test

```sh
VENV=.venv/bin/python
DATA=data/screening/dev

$VENV -m pytest agents/tests -q                      # offline; CASEBOOK_DATA=$DATA adds one test on a real hit
$VENV -m agents.live_check $DATA                     # one call per feature; see "Live check"
$VENV -m agents.second_opinion $DATA --out out/llm/second_opinion.json --limit 10
$VENV -m agents.second_opinion $DATA --out out/llm/second_opinion.json            # the rest; resumes
$VENV -m agents.extract_fallback $DATA --routing out/ml/routing.json --out out/llm/extractions.json
```

Run them from the repo root with `-m`. Do not run a file in `agents/` as a script: `agents/queue.py` would shadow the standard library's `queue`.

`second_opinion` reads `POLICY.md` from the data folder it is given. Options: `--limit N`, `--concurrency N` (default 4), `--redo`. It skips hits already in the output file, saves every 25 results, and prints the engine-versus-model matrix at the end. After it finishes, `POST /api/admin/reload-advisory` makes the server pick the file up.

## How the API is used

Written from the `claude-api` skill's guidance, not from memory:

- Models `claude-sonnet-5-5` and `claude-haiku-5-5`. No `temperature`/`top_p`/`top_k`, no assistant prefill, no forced `tool_choice`: the current models reject all three.
- Every call streams (`messages.stream` + `get_final_message`). Thinking is left adaptive; depth is set with `output_config.effort` (`medium` for the tool loop and the second opinion, `low` for note, queue and extraction). Content blocks are read by type, and each turn's blocks, thinking included, go back unchanged in an append-only history.
- Structured outputs with `output_config.format` for the investigator's final answer, the note, the queue summary, the second opinion and the extraction. Tools are `strict: true`. Eager input streaming is left off on purpose: tool inputs are one short ID, and strict validation is worth more here.
- Prompt caching: a breakpoint on each system block (tools render before system, so the tools, role, rules and policy are one shared prefix across hits and across investigator and chat), plus automatic caching of the growing conversation in the tool loop. The second-opinion batch sends one request first so the parallel ones read the cache it writes.
- **Server-side refusal fallback is enabled** for the interactive model (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`, through `client.beta.messages`). If the API rejects the parameter the request is retried without it and it is dropped for the rest of the process. It is never sent for Haiku. Turn it off with `CASEBOOK_LLM_FALLBACK=0`.
- A refusal or a `max_tokens` stop is checked before any tool is run or any text is parsed, and leads to the fallback.
- The tool loop is a manual loop, not the SDK tool runner, because each turn must be answerable from the cache and the step count is bounded here (6 tool turns, 12 calls). At the limit the last request carries `tool_choice: none` and a mid-conversation `role: "system"` notice (a text block instead on models that do not accept system turns).

## Safety

- **Untrusted text.** Profile notes, allegations, snippets, support notes and quoted lines leave a tool only as `{"untrusted": true, "text": …}`; JSON encoding means the content cannot close its own wrapper. The system prompts say such text is data and cannot instruct. It never appears in a system prompt or in the case block. Step summaries shown to the analyst are written by code, not by the model.
- **Scope.** A `ToolBox` answers only for the records the engine resolved the alert to and for the one hit. Anything else returns an `out_of_scope` error result and discloses nothing.
- **Validation.** Every model output is checked against its schema; a failure falls back and never raises. Cited IDs the tools did not return are dropped. In notes, IDs may come only from the structured fields of the hit's trace and evidence, never from snippets or quotes.
- **Decisions.** No function returns or writes a decision field for a hit. The note's first line (ID, decision, reason code) is written by code from the hit. The second opinion goes to its own file with `agrees` computed here.
- **Tipping off.** A drafted customer request that mentions the list, the hit or the hit's name is replaced by the neutral template.

## What was verified, and how

All offline, no key set:

- `$VENV -m pytest agents/tests -q`: **119 passed** with `CASEBOOK_DATA` set (118 passed, 1 skipped without it). Covered: mock mode for every function and both commands; cache round-trip and offline replay; note ID validation, retry and fallback; tool scoping refusals; prompt injection (offline plan unchanged, injected text only inside the untrusted wrapper, and a scripted model that obeys the injection still cannot call an unknown tool, read another customer's record, change the output shape or touch the decision); event shapes against `contracts/fixtures`; the loop bound; the hit and the data left unmodified. The tests fail any attempt to open a socket.
- The real `anthropic` client (2.12.0) with an in-process `httpx2.MockTransport`: request bodies, the beta header, streaming events, a full two-turn investigator run with a thinking block round-trip, the retry without `fallbacks`, and the mapping of 401/404/429/529 to a fallback. This checks that the requests serialise and the responses parse. It does not check that the API accepts them.
- Mock mode over all 819 dev hits: `investigate`, `write_note` and `chat` each finish without an error event, with the contract's shape, and leave the hit unchanged.
- Each export on `H-ADF453` (alert `A-0DF328`) in mock mode, directly and through the real FastAPI app with `TestClient` (`/api/summary`, `/api/queue/summary`, `/note`, `/investigate`, `/chat`).
- `python -m agents.second_opinion $DATA --limit 10` in mock mode: 10 stand-ins written, matrix printed; a second run resumed.
- `CASEBOOK_DATA=$DATA python3 -m unittest discover -s tests`: 31 tests pass, including the golden hash of the decisions.

## Live check: what is not verified

Run `$VENV -m agents.live_check $DATA` with a key. It forces fresh calls, prints each feature's output and ends with `note=live queue=live investigate=live chat=live second_opinion=live extract=live` and exit code 0. `STUB` or `FAILED` names the feature that fell back. Then run the second-opinion batch with `--limit 10` before the full run.

Unverified until then:

1. That the API accepts the requests at all: the two model IDs for this account, `output_config.effort`, `thinking: {"type": "adaptive", "display": "summarized"}`, top-level `cache_control` together with system-block breakpoints, and the `fallbacks` beta.
2. That the five output schemas compile for structured outputs, in particular the `anyOf` with `null` in the investigation and extraction schemas, and structured output combined with tools in the investigator.
3. The investigator's live loop: which tools the model picks, that it ends with the JSON answer, and the wrap-up turn at the step limit (`tool_choice: none` plus the system-role notice).
4. Chat streaming with tools, and that answers cite IDs inline.
5. The note's live path and its retry turn, which sends the first reply back with its thinking block.
6. The queue summary's live path. Its number check may be stricter than a real answer needs; if it reports `STUB`, look there first.
7. The second opinion on Haiku 5.5, whether prompt caching of the policy prefix takes effect (`usage.cache_read_input_tokens` is stored in each cache entry), and behaviour under rate limits at `--concurrency 4`.
8. Extraction on real routed texts. `out/ml/routing.json` did not exist here, so even the offline run used an invented routing file.
9. Model behaviour: whether the model in fact ignores injected instructions, stays within the lookups it needs, and writes useful findings. The tests prove the plumbing and the backstops, not the model's judgment. No prompt was tuned against real output.

## Known limits and things to know

- The second opinion's offline stand-in is a crude re-reading of the facts (no name-variant list). It agrees with the engine on 817 of 819 dev hits. Its entries carry `stub: true`, `model: "mock"` and a rationale that starts "Offline stand-in, not model output". A run with a key replaces them. Offline, `--redo` is needed to replace stand-ins with cached model answers.
- The model is not given `name_variants.json` for the second opinion, so some disagreements on names will be the model lacking the variant list rather than a real difference.
- When refusal fallback serves a turn, results still report the configured model name, not the fallback model's.
- `chat` keeps the last 20 turns and 4,000 characters per turn.
- The second opinion uses concurrent single requests, as the brief asked. The Message Batches API would halve the cost and was not built; at Haiku prices the full run is small either way.
- An investigator answer that fails validation after live lookups is replaced by a templated result built from those lookups, marked `stub: true`.

## Requests for other streams (not made here)

- `contracts/types.ts`: `SecondOpinion` has no `stub?: boolean`. Stand-in entries carry it anyway; the UI should show it, and `Summary.llm.second_opinions` in `server/state.py` counts stand-ins as second opinions.
- `server/advisory.py::write_note` and `queue_summary` catch only `ImportError`. That is fine: these functions do not raise.
- `requirements.txt` lists `httpx`; the SDK-path tests use `httpx2`, which `anthropic` 2.x installs. They skip if it is missing.
