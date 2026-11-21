/** Incremental SSE decoding for POST fetch responses, including split UTF-8 and CRLF. */
export async function readEventStream<T>(
  response: Response,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
) {
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  if (!response.headers.get("content-type")?.includes("text/event-stream"))
    throw new Error("Expected an event stream from the server.");
  if (!response.body) throw new Error("The response has no stream.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (frame: string) => {
    const data = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (data) onEvent(JSON.parse(data) as T);
  };
  const abort = () => {
    void reader.cancel();
  };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    signal?.throwIfAborted();
    while (true) {
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      buffer += decoder.decode(value, { stream: !done });
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        dispatch(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
      if (done) break;
    }
    if (buffer.trim()) dispatch(buffer);
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
