# Web stream handoff

Implemented entirely inside `web/`. No commits, branch changes, engine edits, server edits, or contract edits were made.

## What is built

- React + Vite + strict TypeScript, with React Router and plain CSS. `src/api/types.ts` and all eleven JSON fixtures are byte-identical copies of the frozen contracts. Contract-copy tests enforce this.
- Queue at `/`: API ordering is preserved; status, practice-label and text filters; hit IDs, blocker groups, optional priority bars; `j`/`k` and Enter navigation; summary metrics and explicit advisory connection status. Filter state lives in the URL.
- Case at `/alerts/:alertId/:hitId?`: exact hit deep links, screened details, hit navigation, engine decision/evidence, candidate switching, comparison table, resolution trail, search-result attribution, watchlist profile, counterfactuals and model signals.
- The comparison ports `customerCell`, `hitCell` and `CODE_CHECK` from the reference, including verified/declared sources, deduplicated names, grouped countries, birth-date provenance/quotes, company labels, report/death checks and decisive-row highlighting.
- Investigator timeline and findings, customer-request copying, streaming chat with inline tool inputs/results and clickable citations, editable/copyable case notes, analyst review and audit history. An override is explicitly marked; no client code changes the engine decision.
- QA at `/qa`: practice labels and false-clear warnings, engine/second-opinion matrix, disagreements, near misses, anomalies, review blockers and case links.
- Command palette on Cmd+K or Ctrl+K searches both customers and alerts. Record chips open a fetch-backed source drawer throughout the app.
- The live transport uses `fetch` POST plus a stream reader for both SSE endpoints. It handles split UTF-8, CRLF, multiple frames, partial chunks, aborts and errors. Streams occupy fixed-height scroll areas and stop on navigation.
- Reference colour tokens, system fonts, automatic light/dark themes, focus rings, native modal dialogs, skip link, responsive layouts, and horizontally contained tables with sticky headers. Loading, error/retry and empty states are supplied for requests. Null model scores are shown as unscored/disconnected; stub responses are identified.

## Run

Use Node 24 or newer (verified with Node 24.4.1 and npm 11.4.2). From the repository root:

```sh
cd web
npm install
VITE_MOCK=1 npm run dev
```

Open `http://127.0.0.1:5173/`. The banner links directly to `/alerts/A-0DF328/H-ADF453`, the fixture investigation/chat/note example.

For the live API, run `npm run dev` without `VITE_MOCK`. Vite proxies `/api` to `http://localhost:8000`. To start the server with the supplied development data, run this from the repository root in another terminal:

```sh
PYTHONDONTWRITEBYTECODE=1 \
CASEBOOK_DATA=data/screening/dev \
CASEBOOK_OUT="$PWD/web/.cache/server" \
.venv/bin/python \
  -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

`CASEBOOK_OUT` above isolates the test audit database inside `web/`.

```sh
cd web
npm run build
npm test
npm run smoke:api
# Only against the disposable test audit database:
npm run smoke:api -- --write-review
```

The normal build is live mode. `VITE_MOCK=1 npm run build` produces a standalone fixture build. The existing FastAPI static mount serves `web/dist`, `/assets/*` and the client route fallback; no server changes are needed. Start the server after the first build so its static mount is registered.

`.npmrc` uses `web/.cache/npm` because the session's global npm cache was unwritable. Build files, caches, browser output and dependencies are ignored within `web/`.

## Verification completed

- `npm install`: succeeded; npm reported no vulnerabilities.
- `npm run build`: passed, including TypeScript checks. Live build output is present in `dist/`.
- `npm test`: **27 tests passed across six files**. These include reference comparison behavior, companies and death/report dates, source chips, missing model signals, disagreements, SSE boundaries/error/cancellation behavior, fetch POST bodies, contract-copy integrity, complete fixture-backed workflows and structural accessibility checks.
- Queue checked with rendered React/Testing Library interactions: eleven fixture alerts, unchanged first row, status/label/text filters, blocker filter, empty results and `j`/`k`/Enter navigation.
- Case checked at the required exact deep link: selected hit, fetched KYC drawer, candidate switching, investigator completion and cancellation/restart, streamed chat, edited/copied note, stub provenance, analyst override submission and resulting audit entry. Tests confirm the engine banner still says Review afterward.
- QA checked by rendering all sections and asserting four practice-label rows, a three-row matrix, six disagreement rows and correct case-link destinations.
- Customer search checked by opening Cmd+K, searching fixture customers and alerts and following an alert result.
- Axe structural WCAG checks passed for queue, case and QA in JSDOM. Colour contrast was explicitly disabled because JSDOM cannot measure rendered contrast; this is not a browser accessibility certification.
- Against the real FastAPI server and the supplied development data, `smoke-api.mjs --write-review` passed for summary, all 440 alert rows, case detail, source records, customer search, QA, queue summary, note, both POST SSE streams, review and audit. The complete serialized hit was identical before and after advisory requests and an analyst override.
- HTTP checks confirmed the live Vite `/api` proxy, mock and live dev-server deep links, and FastAPI responses for `/`, `/qa`, `/alerts/A-0DF328/H-ADF453` and referenced JavaScript under `/assets/`.
- Git status contained only the new `web/` directory; existing repository files were untouched.

## Limits and remaining verification

- **Browser visual QA could not run in this session.** The initial Playwright attempt failed before any page opened: sandboxed Chrome launch terminated with SIGABRT/EPERM. The browser tool reported its host unavailable, and native Chrome access was denied. No screenshots, real-browser contrast results, narrow-screen visual checks or native dialog focus checks are claimed.
- Six optional Playwright scenarios are provided in `tests/workbench.spec.ts` for those checks. They have not passed in this environment. In a browser-capable environment, stop any server using port 5174, then run from `web/`:

  ```sh
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" npx playwright install chromium
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/playwright" npm run test:e2e
  ```

  The suite starts its own fixture dev server and checks workflows, light/dark contrast, narrow-screen table containment and screenshots. Review its results before treating visual acceptance as verified.
- Frozen fixtures are samples: eleven alerts/31 hits and 164 source records, while summary/QA/blocker counts describe the full dataset. The banner explains this. Some palette customers and blocker IDs have no corresponding fixture record/alert; their drawers show a clear unavailable-record error. QA supplies case links wherever an alert mapping exists, and keeps every remaining ID inspectable as a chip. The live alert list supplies the complete mapping.
- Note, investigation and chat fixtures describe H-ADF453. Mock mode replays those responses only for that hit; other hits report the sample limitation rather than showing another customer's findings. Mock reviews live only in memory and reset on reload.
- Actual trained models and live LLM responses were not available; live checks exercised the server's explicitly marked stand-ins. Backend exceptions and validation failures are surfaced rather than replaced with fabricated results.
- No changes outside `web/` are requested. No engine golden tests were rerun; source-scope checks and the live before/after hit comparison establish the frontend's engine-immutability check.
