import { useEffect, useRef, useState, type FormEvent } from "react";
import type {
  CaseNote,
  ChatEvent,
  InvestigateEvent,
  InvestigationResult,
} from "../api/types";
import { errorText, post, stream } from "../api/client";
import { CopyButton, ErrorMessage, Ids, Rich } from "./common";

function useController() {
  const ref = useRef<AbortController | null>(null);
  useEffect(() => () => ref.current?.abort(), []);
  return {
    start: () => {
      ref.current?.abort();
      const controller = new AbortController();
      ref.current = controller;
      return controller;
    },
    stop: () => ref.current?.abort(),
  };
}
type Step = Extract<ChatEvent, { type: "step" }>;
function ToolStep({ step }: { step: Step }) {
  return (
    <details className="tool-step">
      <summary>
        {step.index + 1}. {step.tool}
      </summary>
      <pre>
        <Rich>{JSON.stringify(step.input, null, 2)}</Rich>
      </pre>
      <p>
        <Rich>{step.summary}</Rich>
      </p>
    </details>
  );
}
function Provenance({
  stub,
  cached,
  model,
}: {
  stub?: boolean;
  cached?: boolean;
  model?: string | null;
}) {
  return (
    <p className="quiet provenance">
      {stub
        ? "Not connected · deterministic stand-in"
        : model
          ? `Model: ${model}`
          : "Model not specified"}
      {cached ? " · Cached" : ""}
    </p>
  );
}
export function Investigator({ hitId }: { hitId: string }) {
  const [events, setEvents] = useState<InvestigateEvent[]>([]);
  const [result, setResult] = useState<InvestigationResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useController();
  async function investigate() {
    const run = controller.start();
    setBusy(true);
    setError("");
    setEvents([]);
    setResult(undefined);
    let final = false;
    try {
      await stream<InvestigateEvent>(
        `/hits/${hitId}/investigate`,
        {},
        (event) => {
          if (event.type === "error") throw new Error(event.message);
          if (event.type === "final") {
            final = true;
            setResult(event.result);
          } else setEvents((current) => [...current, event]);
        },
        run.signal,
      );
      if (!final)
        throw new Error(
          "Investigation ended before a final result arrived. Try again.",
        );
    } catch (e) {
      if (!run.signal.aborted) setError(errorText(e));
    } finally {
      if (!run.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="section-bar">
        <h2>Investigator</h2>
        <button onClick={investigate} disabled={busy}>
          {busy ? "Investigating…" : "Investigate"}
        </button>
      </div>
      <p className="quiet">Follow the evidence and draft the next request.</p>
      <div
        className="stream-window investigation"
        role="log"
        aria-label="Investigation timeline"
        aria-live="polite"
        aria-busy={busy}
      >
        {!events.length && !busy && (
          <p className="empty">
            Run an investigation to see the tool trail and findings.
          </p>
        )}
        <ol className="trail">
          {events.map((event, i) => (
            <li key={i}>
              {event.type === "thought" ? (
                <p>
                  <Rich>{event.text}</Rich>
                </p>
              ) : event.type === "step" ? (
                <ToolStep step={event} />
              ) : null}
            </li>
          ))}
        </ol>
        {result && (
          <div className="investigation-result">
            <h3>
              <Rich>{result.blocker}</Rich>
            </h3>
            {result.findings.map((finding, i) => (
              <p key={i}>
                <Rich>{finding.text}</Rich> <Ids ids={finding.cited_ids} />
              </p>
            ))}
            <h3>Next action</h3>
            <p>
              <Rich>{result.next_action}</Rich>
            </p>
            {result.rfi_draft && (
              <>
                <div className="section-bar">
                  <h3>Customer request draft</h3>
                  <CopyButton text={result.rfi_draft} />
                </div>
                <blockquote className="pre-wrap">
                  <Rich>{result.rfi_draft}</Rich>
                </blockquote>
              </>
            )}
            <Ids ids={result.cited_ids} />
            <Provenance {...result} />
          </div>
        )}
      </div>
      <ErrorMessage message={error} />
      {busy && (
        <button
          onClick={() => {
            controller.stop();
            setBusy(false);
            setError("Investigation stopped. Partial steps are shown above.");
          }}
        >
          Stop investigation
        </button>
      )}
    </section>
  );
}
interface Message {
  role: "user" | "assistant";
  content: string;
  steps: Step[];
  ids: string[];
  stub?: boolean;
  incomplete?: boolean;
}
export function Chat({ hitId }: { hitId: string }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useController();
  async function send(e: FormEvent) {
    e.preventDefault();
    if (!question.trim() || busy) return;
    const user: Message = {
      role: "user",
      content: question.trim(),
      steps: [],
      ids: [],
    };
    const history = [...messages.filter((m) => !m.incomplete), user];
    const index = messages.length + 1;
    setMessages([
      ...messages,
      user,
      { role: "assistant", content: "", steps: [], ids: [], incomplete: true },
    ]);
    setQuestion("");
    setBusy(true);
    setError("");
    const run = controller.start();
    const update = (fn: (message: Message) => Message) =>
      setMessages((current) =>
        current.map((m, i) => (i === index ? fn(m) : m)),
      );
    let done = false;
    try {
      await stream<ChatEvent>(
        `/hits/${hitId}/chat`,
        { messages: history.map(({ role, content }) => ({ role, content })) },
        (event) => {
          if (event.type === "error") throw new Error(event.message);
          if (event.type === "text")
            update((m) => ({ ...m, content: m.content + event.delta }));
          if (event.type === "step")
            update((m) => ({ ...m, steps: [...m.steps, event] }));
          if (event.type === "done") {
            done = true;
            update((m) => ({
              ...m,
              ids: event.cited_ids,
              stub: event.stub,
              incomplete: false,
            }));
          }
        },
        run.signal,
      );
      if (!done)
        throw new Error(
          "Chat ended before the answer was complete. Please ask again.",
        );
    } catch (e) {
      if (!run.signal.aborted) setError(errorText(e));
    } finally {
      if (!run.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Ask Casebook</h2>
      <div
        className="stream-window chat"
        role="log"
        aria-label="Casebook conversation"
        aria-live="polite"
        aria-busy={busy}
      >
        {!messages.length && (
          <p className="empty">
            Ask about the decision, evidence or next action.
          </p>
        )}
        {messages.map((m, i) => (
          <article className={`message ${m.role}`} key={i}>
            <b>{m.role === "user" ? "You" : "Casebook"}</b>
            {m.steps.map((step, j) => (
              <ToolStep step={step} key={j} />
            ))}
            <p className="pre-wrap">
              <Rich>
                {m.content ||
                  (busy ? "Reading the evidence…" : "No answer received.")}
              </Rich>
            </p>
            <Ids ids={m.ids} />
            {m.stub && (
              <p className="quiet">LLM not connected · stand-in answer</p>
            )}
            {m.incomplete && !busy && <small>Incomplete answer</small>}
          </article>
        ))}
      </div>
      <form onSubmit={send}>
        <label>
          Question
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Why does this hit need review?"
            rows={2}
            disabled={busy}
          />
        </label>
        <div className="section-bar">
          <span className="quiet">Answers cite source records.</span>
          <button className="primary" disabled={busy || !question.trim()}>
            {busy ? "Answering…" : "Send question"}
          </button>
        </div>
      </form>
      <ErrorMessage message={error} />
      {busy && (
        <button
          onClick={() => {
            controller.stop();
            setBusy(false);
            setError("Answer stopped. You can ask again.");
          }}
        >
          Stop answer
        </button>
      )}
    </section>
  );
}
export function CaseNotePanel({ hitId }: { hitId: string }) {
  const [note, setNote] = useState<CaseNote>();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useController();
  async function generate() {
    const run = controller.start();
    setBusy(true);
    setError("");
    try {
      const result = await post<CaseNote>(
        `/hits/${hitId}/note`,
        undefined,
        run.signal,
      );
      if (!run.signal.aborted) {
        setNote(result);
        setText(result.note);
      }
    } catch (e) {
      if (!run.signal.aborted) setError(errorText(e));
    } finally {
      if (!run.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="section-bar">
        <h2>Case note</h2>
        <button onClick={generate} disabled={busy}>
          {busy ? "Drafting…" : "Draft note"}
        </button>
      </div>
      <p className="quiet">Editable draft for your case file.</p>
      <ErrorMessage message={error} />
      {note ? (
        <>
          <Provenance {...note} />
          <label>
            Case note draft
            <textarea
              rows={7}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <details>
            <summary>Preview with source links</summary>
            <p className="pre-wrap">
              <Rich>{text}</Rich>
            </p>
          </details>
          <div className="section-bar">
            <span>
              <Ids ids={note.cited_ids} />
            </span>
            <CopyButton text={text} />
          </div>
        </>
      ) : (
        <p className="empty">No note drafted yet.</p>
      )}
    </section>
  );
}
