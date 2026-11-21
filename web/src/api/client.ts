import type { AuditEntry, ChatEvent, InvestigateEvent } from "./types";
import { readEventStream } from "./stream";
export const MOCK = import.meta.env.VITE_MOCK === "1";
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  if (MOCK) return (await import("./mock")).mockRequest<T>(path, options);
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json();
}
export const post = <T>(path: string, body?: unknown, signal?: AbortSignal) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
export type ReviewInput = Pick<
  AuditEntry,
  "decision" | "reason_code" | "comment" | "analyst"
>;
export async function stream<T extends ChatEvent | InvestigateEvent>(
  path: string,
  body: unknown,
  onEvent: (event: T) => void,
  signal: AbortSignal,
) {
  const response = MOCK
    ? await (await import("./mock")).mockStream(path, signal)
    : await fetch(`/api${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(body),
        signal,
      });
  await readEventStream(response, onEvent, signal);
}
