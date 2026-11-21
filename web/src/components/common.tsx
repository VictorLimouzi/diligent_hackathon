import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Decision, SourceRecord } from "../api/types";
import { errorText } from "../api/client";
import { useRequest } from "../api/useRequest";

export const decisions: Decision[] = [
  "DIFFERENT_ENTITY",
  "SAME_ENTITY",
  "REVIEW",
];
export const words: Record<Decision, string> = {
  DIFFERENT_ENTITY: "Different entity",
  SAME_ENTITY: "Same entity",
  REVIEW: "Review",
};
export const pct = (n: number) => `${Math.round(n * 100)}%`;
export function DecisionChip({ decision }: { decision: Decision }) {
  return <span className={`chip ${decision}`}>{words[decision]}</span>;
}
export function ErrorMessage({
  message,
  retry,
}: {
  message?: string;
  retry?: () => void;
}) {
  return message ? (
    <div role="alert" className="error">
      {message} {retry && <button onClick={retry}>Try again</button>}
    </div>
  ) : null;
}
export function Loading() {
  return (
    <p className="loading" role="status">
      Loading…
    </p>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}
export function Modal({
  title,
  close,
  children,
  className = "",
}: {
  title: string;
  close: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={className}
      aria-label={title}
      onCancel={close}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="modal-header">
        <h2>{title}</h2>
        <button onClick={close} aria-label={`Close ${title}`}>
          Close <kbd>Esc</kbd>
        </button>
      </div>
      {children}
    </dialog>
  );
}
const RecordsContext = createContext<(id: string) => void>(() => {});
export function Id({ id }: { id?: string | null }) {
  const open = useContext(RecordsContext);
  return id ? (
    <button
      type="button"
      className="id"
      onClick={() => open(id)}
      aria-label={`Open record ${id}`}
    >
      {id}
    </button>
  ) : null;
}
export function Ids({ ids }: { ids: string[] }) {
  return (
    <>
      {[...new Set(ids)].map((id) => (
        <Id key={id} id={id} />
      ))}
    </>
  );
}
export function Rich({ children }: { children: string | null | undefined }) {
  return (
    <>
      {String(children ?? "")
        .split(/(\b(?:CUS|KYC|NOTE|SR|H)-[A-Z0-9]+\b)/g)
        .map((text, i) =>
          /^(?:CUS|KYC|NOTE|SR|H)-[A-Z0-9]+$/.test(text) ? (
            <Id key={i} id={text} />
          ) : (
            text
          ),
        )}
    </>
  );
}
export function Fields({ fields }: { fields: Record<string, string> }) {
  return (
    <dl className="fields">
      {Object.entries(fields).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>
            <Rich>{value}</Rich>
          </dd>
        </div>
      ))}
    </dl>
  );
}
function RecordDrawer({ id, close }: { id: string; close: () => void }) {
  const { data, loading, error, reload } = useRequest<SourceRecord>(
    `/records/${encodeURIComponent(id)}`,
  );
  return (
    <Modal title="Source record" close={close} className="drawer">
      <p className="eyebrow">Original evidence</p>
      <h2>{id}</h2>
      {loading && <Loading />}
      <ErrorMessage message={error} retry={reload} />
      {data && (
        <>
          <p className="quiet">{data.kind}</p>
          <Fields fields={data.fields} />
        </>
      )}
    </Modal>
  );
}
export function RecordsProvider({ children }: { children: ReactNode }) {
  const [id, setId] = useState<string | null>(null);
  return (
    <RecordsContext.Provider value={setId}>
      {children}
      {id && <RecordDrawer id={id} close={() => setId(null)} />}
    </RecordsContext.Provider>
  );
}
export function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState("Copy");
  const [error, setError] = useState("");
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setState("Copied");
            setError("");
          } catch (e) {
            setError(errorText(e));
          }
        }}
      >
        {state}
      </button>
      <ErrorMessage message={error} />
    </>
  );
}
