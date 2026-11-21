import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});
describe("live API transport", () => {
  it("uses fetch POST for streaming chat and sends the contracted conversation", async () => {
    vi.stubEnv("VITE_MOCK", "0");
    vi.resetModules();
    const { stream } = await import("./client");
    const body = { messages: [{ role: "user", content: "Explain this hit." }] };
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('data: {"type":"done","cited_ids":[]}\n\n', {
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];
    await stream("/hits/H-ADF453/chat", body, (e) => events.push(e), signal);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/hits/H-ADF453/chat",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(body),
        signal,
      }),
    );
    expect(events).toEqual([{ type: "done", cited_ids: [] }]);
  });
  it("preserves server error details and serializes analyst review bodies", async () => {
    vi.stubEnv("VITE_MOCK", "0");
    vi.resetModules();
    const { request, post } = await import("./client");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response('{"detail":"Unknown hit"}', { status: 404 }),
      )
      .mockResolvedValueOnce(new Response('{"id":2}'));
    vi.stubGlobal("fetch", fetchMock);
    await expect(request("/alerts/missing")).rejects.toThrow("Unknown hit");
    const review = {
      decision: "REVIEW",
      reason_code: "INSUFFICIENT_DATA",
      comment: "More evidence required.",
      analyst: "analyst",
    };
    await expect(post("/hits/H-ADF453/review", review)).resolves.toEqual({
      id: 2,
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/hits/H-ADF453/review",
      expect.objectContaining({ method: "POST", body: JSON.stringify(review) }),
    );
  });
});
