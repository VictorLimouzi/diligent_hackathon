# Brief: frontend (`web/`)

Read `docs/briefs/COMMON.md` first. You own `web/` only.

## Goal

A clean, fast analyst app for the Casebook API: React + Vite + TypeScript, no UI framework heavier than you need. An analyst should see at a glance what decided each hit, open any source record in one click, and know what to do next on a REVIEW.

## Contract

- Types: copy `contracts/types.ts` into `web/src/api/types.ts` unchanged.
- Endpoints are listed as comments in that file; the server is `server/app.py` on `http://localhost:8000`. Use a Vite dev proxy for `/api`.
- Fixtures for every endpoint are in `contracts/fixtures/`. Build a mock mode (`VITE_MOCK=1`) that serves them, so the app runs with no server: `alerts.json` (list), `alert_details.json` (keyed by alert id), `records.json` (keyed by record id), `summary.json`, `qa.json`, `queue_summary.json`, `search_customers.json`, `note.json`, `investigate_events.json` and `chat_events.json` (replay as a stream with small delays), `audit.json`.
- The two streaming endpoints are POST requests returning `text/event-stream` with `data: <json>\n\n` lines: read them with `fetch` and a stream reader, not `EventSource`.
- `ml` and `llm` on a hit can be `null`, and any response may carry `stub: true`: render an honest empty or "not connected" state, never fake numbers.

## Screens

1. **Queue** (`/`): alert list from `GET /api/alerts`, default order as returned (open first, by priority). Status chip, hit count, blockers, priority bar when present. Filters by status and "labelled", text search, keyboard `j`/`k` to move and `Enter` to open. Header strip from `GET /api/summary`: alerts cleared, decision mix, review rate, practice-label score, whether ML and the LLM are connected. A collapsible "queue summary" from `GET /api/queue/summary` grouping REVIEW hits by blocker; clicking a group filters the queue.
2. **Case** (`/alerts/:alertId/:hitId?`): 
   - Alert header (screened details) and a tab per hit with its decision.
   - Decision banner: decision, reason code, summary, evidence chips, customer record.
   - Comparison table, one row per check (`trace.candidates[i].checks`): Field | Customer | Watchlist profile | Result. Highlight the rows that decided the hit. Port the logic from `workbench/template.html` (functions `customerCell`, `hitCell`, `CODE_CHECK`), which already renders this correctly.
   - Candidate cards when `trace.candidates.length > 1`; selecting one switches the table.
   - **What would settle it**: `counterfactuals`, each as a question with its outcomes as decision chips.
   - **Model signals**: `ml.priority`, `ml.drivers` as a small signed bar list, near-miss warning, and the LLM second opinion with an "agrees/disagrees" marker.
   - **Investigator**: a button that calls `POST …/investigate` and renders the stream as a timeline (thoughts, tool steps with their input and summary), then the final result: blocker, findings with ID chips, next action, and the drafted customer request with a copy button.
   - **Ask Casebook**: chat panel on `POST …/chat`, streaming text, tool steps shown inline, record IDs in answers rendered as clickable chips.
   - **Case note**: button for `POST …/note`, editable result, copy button.
   - **Analyst decision bar**: choose decision + reason code + comment, `POST …/review`; show the hit's audit history from `GET /api/audit?hit_id=`. Mark clearly when the analyst overrides the engine.
   - How the customer was found (`trace.resolution` + candidate `path`), search results considered (used / about the hit / excluded with reason), the watchlist profile.
   - Any record ID anywhere (`CUS-…`, `KYC-…`, `NOTE-…`, `SR-…`, `H-…`) is a chip that opens a side drawer from `GET /api/records/{id}`.
3. **QA** (`/qa`): from `GET /api/qa`. Practice-label table (match / mismatch, false clears called out), engine vs second-opinion matrix and disagreement list, near-misses, unusual clears, blockers. Every row links to its case.
4. **Customer search**: a command-palette style search (`⌘K`) over `GET /api/search/customers` and alerts.

## Design

- Calm, dense, professional: this is a compliance tool, not a marketing page. System font, 14px base, generous table alignment, restrained colour.
- Colour meaning (reuse the tokens in `workbench/template.html`): amber = REVIEW, red-tinted = SAME_ENTITY (goes to compliance), green-tinted = DIFFERENT_ENTITY (cleared). Never use colour alone: every chip has a text label.
- Light and dark themes via `prefers-color-scheme`; visible focus rings; all controls keyboard reachable; tables keep their headers readable on narrow screens.
- No layout shift while streams arrive; loading and error states for every request.

## Acceptance

- `cd web && npm install && npm run build` succeeds with no TypeScript errors; `npm run dev` with `VITE_MOCK=1` shows every screen populated from fixtures.
- `web/dist` is a static build the FastAPI server can serve at `/` (relative asset paths under `/assets`, client-side routing with an `index.html` fallback).
- A deep link `/alerts/A-0DF328/H-ADF453` opens that hit.
- Add a handful of component tests (Vitest) for the comparison table and the stream reader; `npm test` passes.
- `web/NOTES.md` as described in COMMON.md, including how you checked each screen.
