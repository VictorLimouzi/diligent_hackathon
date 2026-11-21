import type {
  AlertDetail,
  AlertRow,
  AuditEntry,
  CustomerMatch,
  SourceRecord,
} from "./types";
import type { ReviewInput } from "./client";
import alertsData from "./fixtures/alerts.json";
import detailsData from "./fixtures/alert_details.json";
import recordsData from "./fixtures/records.json";
import summary from "./fixtures/summary.json";
import qa from "./fixtures/qa.json";
import queueSummary from "./fixtures/queue_summary.json";
import customersData from "./fixtures/search_customers.json";
import note from "./fixtures/note.json";
import auditData from "./fixtures/audit.json";
import investigateEvents from "./fixtures/investigate_events.json";
import chatEvents from "./fixtures/chat_events.json";
import benchmarkSample from "./benchmark.sample.json";

const details = detailsData as unknown as Record<string, AlertDetail>;
const records = recordsData as Record<string, SourceRecord>;
const audit = structuredClone(auditData) as AuditEntry[];
const hits = Object.values(details).flatMap((a) => a.hits);
export async function mockRequest<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  options?.signal?.throwIfAborted();
  const url = new URL(path, "http://mock");
  const p = url.pathname;
  let value: unknown;
  if (p === "/alerts") {
    const q = url.searchParams.get("q")?.toLowerCase() || "";
    value = (alertsData as AlertRow[]).filter(
      (a) =>
        (!url.searchParams.get("status") ||
          a.status === url.searchParams.get("status")) &&
        (url.searchParams.get("labelled") !== "true" || a.labelled) &&
        [a.alert_id, a.customer_name, ...a.hit_ids, ...a.hit_names].some((x) =>
          x.toLowerCase().includes(q),
        ),
    );
  } else if (p.startsWith("/alerts/"))
    value = details[decodeURIComponent(p.slice(8))];
  else if (p.startsWith("/records/"))
    value = records[decodeURIComponent(p.slice(9))];
  else if (p === "/summary") value = summary;
  else if (p === "/qa") value = qa;
  else if (p === "/benchmark") value = benchmarkSample;
  else if (p === "/queue/summary") value = queueSummary;
  else if (p === "/search/customers") {
    const q = (url.searchParams.get("q") || "")
      .toLocaleLowerCase()
      .normalize("NFD")
      .replace(/\p{Diacritic}/gu, "");
    value = q
      ? (customersData as CustomerMatch[])
          .filter((c) =>
            `${c.full_name} ${c.record_id}`
              .toLowerCase()
              .normalize("NFD")
              .replace(/\p{Diacritic}/gu, "")
              .includes(q),
          )
          .slice(0, Number(url.searchParams.get("k") || 10))
      : [];
  } else if (p === "/audit")
    value = audit.filter(
      (a) =>
        !url.searchParams.get("hit_id") ||
        a.hit_id === url.searchParams.get("hit_id"),
    );
  else if (/^\/hits\/[^/]+\/note$/.test(p)) {
    if (p.split("/")[2] !== "H-ADF453")
      throw new Error("The sample note is available for H-ADF453 only.");
    value = note;
  } else if (/^\/hits\/[^/]+\/review$/.test(p) && options?.method === "POST") {
    const hit = hits.find((h) => h.hit_id === p.split("/")[2]);
    if (hit) {
      const input = JSON.parse(String(options.body)) as ReviewInput;
      const entry: AuditEntry = {
        ...input,
        id: Math.max(0, ...audit.map((a) => a.id)) + 1,
        at: Date.now() / 1000,
        hit_id: hit.hit_id,
        alert_id: hit.alert_id,
        engine_decision: hit.decision,
        engine_reason_code: hit.reason_code,
        overrides_engine: hit.decision !== input.decision,
      };
      audit.push(entry);
      value = entry;
    }
  }
  if (value === undefined)
    throw new Error(
      "This record or endpoint is not included in the fixture sample.",
    );
  return structuredClone(value) as T;
}
export async function mockStream(
  path: string,
  signal: AbortSignal,
): Promise<Response> {
  if (path.split("/")[2] !== "H-ADF453")
    throw new Error(
      "Sample investigation and chat are available for H-ADF453 only.",
    );
  const events = path.endsWith("/investigate") ? investigateEvents : chatEvents;
  let index = 0;
  let timer: ReturnType<typeof setTimeout>;
  let active = true;
  let finish: () => void;
  const cleanup = () => {
    active = false;
    clearTimeout(timer);
    signal.removeEventListener("abort", finish);
  };
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      finish = () => {
        if (!active) return;
        cleanup();
        controller.close();
      };
      const next = () => {
        if (signal.aborted || index === events.length) {
          finish();
          return;
        }
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(events[index++])}\n\n`),
        );
        timer = setTimeout(next, 180);
      };
      signal.addEventListener("abort", finish, { once: true });
      next();
    },
    cancel() {
      cleanup();
    },
  });
  return new Response(body, {
    headers: { "Content-Type": "text/event-stream" },
  });
}
