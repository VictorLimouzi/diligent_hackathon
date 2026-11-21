import assert from "node:assert/strict";
import { readEventStream } from "../src/api/stream.ts";

// Run against a local server with a disposable CASEBOOK_OUT for --write-review.
const base = process.env.CASEBOOK_URL || "http://127.0.0.1:8000";
const aid = "A-0DF328";
const hid = "H-ADF453";
async function json(path, body) {
  const response = await fetch(
    `${base}/api${path}`,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  assert.equal(response.status, 200, path);
  return response.json();
}
const summary = await json("/summary");
assert(summary.alerts > 0);
const alerts = await json("/alerts");
assert.equal(alerts.length, summary.alerts);
assert(alerts.some((a) => a.alert_id === aid));
const detail = await json(`/alerts/${aid}`);
const hit = detail.hits.find((h) => h.hit_id === hid);
assert(hit);
const engineBefore = JSON.stringify(hit);
assert.equal((await json(`/records/${hit.evidence[0]}`)).id, hit.evidence[0]);
assert((await json("/search/customers?q=Markus&k=10")).length > 0);
assert((await json("/qa")).label_metrics.n > 0);
assert((await json("/queue/summary")).groups.length > 0);
assert.equal(typeof (await json(`/hits/${hid}/note`, {})).note, "string");
for (const action of ["investigate", "chat"]) {
  const events = [];
  const response = await fetch(`${base}/api/hits/${hid}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(
      action === "chat"
        ? { messages: [{ role: "user", content: "Explain the next action." }] }
        : {},
    ),
  });
  await readEventStream(response, (event) => events.push(event));
  assert(!events.some((e) => e.type === "error"));
  assert.equal(events.at(-1).type, action === "chat" ? "done" : "final");
}
if (process.argv.includes("--write-review")) {
  const review = await json(`/hits/${hid}/review`, {
    decision: "SAME_ENTITY",
    reason_code: "NAME_DOB_MATCH",
    comment: "Frontend integration test in disposable audit database.",
    analyst: "web-smoke-test",
  });
  assert(review.overrides_engine);
  const audit = await json(`/audit?hit_id=${hid}`);
  assert(audit.some((a) => a.id === review.id && a.overrides_engine));
}
assert.equal(
  JSON.stringify(
    (await json(`/alerts/${aid}`)).hits.find((h) => h.hit_id === hid),
  ),
  engineBefore,
  "Engine hit must remain unchanged after advisory calls and analyst review",
);
for (const path of ["/", `/alerts/${aid}/${hid}`, "/qa"]) {
  const response = await fetch(`${base}${path}`);
  assert.equal(response.status, 200);
  const html = await response.text();
  assert(html.includes('<div id="root"></div>'));
  const asset = html.match(/src="(\/assets\/[^\"]+)"/)?.[1];
  assert(asset, "Built assets must resolve from deep links");
  const file = await fetch(`${base}${asset}`);
  assert.equal(file.status, 200);
  assert(file.headers.get("content-type").includes("javascript"));
}
console.log(
  "Live API, POST streams, source records, static assets, SPA fallback and engine immutability passed.",
);
