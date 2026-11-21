import { describe, expect, it } from "vitest";
import { readEventStream } from "./stream";
const encoder = new TextEncoder();
function response(chunks: Uint8Array[]) {
  return new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((c) => controller.enqueue(c));
        controller.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream; charset=utf-8" } },
  );
}
describe("POST event stream reader", () => {
  it("decodes arbitrary byte boundaries, UTF-8, multiple frames, comments and CRLF", async () => {
    const data = encoder.encode(
      ':keepalive\r\n\r\ndata: {"type":"text","delta":"Müller"}\r\n\r\ndata: {"type":"done","cited_ids":[]}\n\n',
    );
    const events: unknown[] = [];
    await readEventStream(
      response(Array.from(data, (byte) => Uint8Array.of(byte))),
      (e) => events.push(e),
    );
    expect(events).toEqual([
      { type: "text", delta: "Müller" },
      { type: "done", cited_ids: [] },
    ]);
  });
  it("handles multi-line data and a final frame without a separator", async () => {
    const events: unknown[] = [];
    await readEventStream(
      response([
        encoder.encode('data: {"type":"thought",\ndata: "text":"Evidence"}'),
      ]),
      (e) => events.push(e),
    );
    expect(events).toEqual([{ type: "thought", text: "Evidence" }]);
  });
  it("reports HTTP errors, wrong response types and malformed JSON", async () => {
    await expect(
      readEventStream(new Response("error", { status: 503 }), () => {}),
    ).rejects.toThrow("503");
    await expect(readEventStream(new Response("{}"), () => {})).rejects.toThrow(
      "Expected an event stream",
    );
    await expect(
      readEventStream(
        response([encoder.encode("data: invalid\n\n")]),
        () => {},
      ),
    ).rejects.toThrow();
  });
  it("cancels a pending read when the request is aborted", async () => {
    let cancelled = false;
    const run = new AbortController();
    const pending = new Response(
      new ReadableStream({
        cancel() {
          cancelled = true;
        },
      }),
      { headers: { "Content-Type": "text/event-stream" } },
    );
    const promise = readEventStream(pending, () => {}, run.signal);
    run.abort();
    await expect(promise).rejects.toThrow();
    expect(cancelled).toBe(true);
  });
  it("propagates event-handler failures and releases the reader", async () => {
    const r = response([
      encoder.encode('data: {"type":"error","message":"Disconnected"}\n\n'),
    ]);
    await expect(
      readEventStream(r, () => {
        throw new Error("Disconnected");
      }),
    ).rejects.toThrow("Disconnected");
    expect(r.body!.locked).toBe(false);
  });
});
