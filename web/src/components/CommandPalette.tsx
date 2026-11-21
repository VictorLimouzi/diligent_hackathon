import { useDeferredValue, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { AlertRow, CustomerMatch } from "../api/types";
import { useRequest } from "../api/useRequest";
import { ErrorMessage, Id, Loading, Modal, pct, Rich } from "./common";
export function CommandPalette({ close }: { close: () => void }) {
  const [query, setQuery] = useState("");
  const q = useDeferredValue(query.trim());
  // Opened from a case: the alert's screened details put the right namesake first.
  const alertId = useLocation().pathname.match(/^\/alerts\/([^/]+)/)?.[1];
  const from = alertId ? `&alert_id=${encodeURIComponent(alertId)}` : "";
  const customers = useRequest<CustomerMatch[]>(
    q ? `/search/customers?q=${encodeURIComponent(q)}&k=10${from}` : null,
  );
  const alerts = useRequest<AlertRow[]>(
    q ? `/alerts?q=${encodeURIComponent(q)}` : null,
  );
  return (
    <Modal title="Search Casebook" close={close} className="palette">
      <label className="search-field">
        Customer, record or alert
        <input
          autoFocus
          type="search"
          placeholder="Search names or IDs…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <div className="palette-results">
        {!q ? (
          <p className="empty">
            Search customers and screening alerts. Use Tab to select a result
            and Enter to open it.
          </p>
        ) : (
          <>
            <h3>Customers</h3>
            {customers.loading && <Loading />}
            <ErrorMessage message={customers.error} retry={customers.reload} />
            {customers.data?.map((c) => (
              <div className="search-result" key={c.record_id}>
                <div>
                  <b>
                    <Rich>{c.full_name}</Rich>
                  </b>
                  <small>
                    {c.dob || "Birth date unknown"} ·{" "}
                    {c.country || "Country unknown"} · {pct(c.score)} match
                  </small>
                </div>
                <Id id={c.record_id} />
              </div>
            ))}
            {customers.data?.length === 0 && (
              <p className="quiet">No customers found.</p>
            )}
            <h3>Alerts</h3>
            {alerts.loading && <Loading />}
            <ErrorMessage message={alerts.error} retry={alerts.reload} />
            {alerts.data?.map((a) => (
              <Link
                key={a.alert_id}
                className="search-result"
                to={`/alerts/${a.alert_id}`}
                onClick={close}
              >
                <span>
                  {a.customer_name}
                  <small>
                    {a.alert_id} · {a.hit_count} hits
                  </small>
                </span>
                <span>Open →</span>
              </Link>
            ))}
            {alerts.data?.length === 0 && (
              <p className="quiet">No alerts found.</p>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
