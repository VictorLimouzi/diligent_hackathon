import type {
  Candidate,
  Check,
  CustomerPicture,
  Hit,
  ReasonCode,
} from "../api/types";
import { Id, Ids, Rich } from "./common";

export const CODE_CHECK: Partial<Record<ReasonCode, Check["name"][]>> = {
  DOB_MISMATCH: ["dob"],
  NAME_MISMATCH_VERIFIED: ["name"],
  LOCATION_MISMATCH: ["location"],
  TEMPORAL_IMPOSSIBILITY: ["time"],
  DECEASED: ["death"],
  ENTITY_TYPE_MISMATCH: ["type"],
  NAME_DOB_LOCATION_MATCH: ["name", "dob", "location"],
  NAME_DOB_MATCH: ["name", "dob"],
  NAME_LOCATION_MATCH: ["name", "location"],
};
function CustomerCell({
  name,
  customer: c,
  screenedAt,
}: {
  name: Check["name"];
  customer: CustomerPicture;
  screenedAt: string;
}) {
  if (name === "type") return <>{c.entity_type || "Not known"}</>;
  if (name === "name") {
    if (c.verified_name)
      return (
        <>
          <span className="fact">
            <Rich>{c.verified_name}</Rich> <Id id={c.verified_check} />
          </span>
          <small>Verified legal name</small>
        </>
      );
    const names = c.declared_names.filter(
      (n, i, all) => all.findIndex((x) => x.value === n.value) === i,
    );
    return (
      <>
        {names.length
          ? names.map((n) => (
              <span className="fact" key={n.value}>
                <Rich>{n.value}</Rich>{" "}
                {n.source.startsWith("CUS") ? (
                  <Id id={n.source} />
                ) : (
                  <small>as screened</small>
                )}
              </span>
            ))
          : "Not known"}
        <small>Declared, not verified</small>
      </>
    );
  }
  if (name === "dob" || name === "time")
    return c.dob ? (
      <>
        <span className="fact">
          {c.dob} <Id id={c.dob_source} />
        </span>
        <small>
          {c.dob_verified ? "From the latest approved check" : "Declared"}
        </small>
      </>
    ) : (
      <>Not known</>
    );
  if (name === "location") {
    const grouped = new Map<string, string[]>();
    c.countries.forEach((country) =>
      grouped.set(country.value, [
        ...(grouped.get(country.value) || []),
        country.source,
      ]),
    );
    return grouped.size ? (
      <>
        {[...grouped].map(([country, ids]) => (
          <span className="fact" key={country}>
            <Rich>{country}</Rich> <Ids ids={ids} />
          </span>
        ))}
      </>
    ) : (
      <>None on record</>
    );
  }
  return <>Screened {screenedAt || "date not known"}</>;
}
function HitCell({ name, hit }: { name: Check["name"]; hit: Hit }) {
  const f = hit.trace!.hit;
  if (name === "type") return <>{hit.profile.entity_type || "Not known"}</>;
  if (name === "name")
    return (
      <>
        {f.names.map((n) => (
          <span className="fact" key={n}>
            <Rich>{n}</Rich>
          </span>
        ))}
      </>
    );
  if (name === "dob") {
    if (!f.dobs.length) return <>None found in the profile or search results</>;
    const origin = {
      "dobs field": "Listed on the profile",
      "profile notes": "From the profile notes",
      "search results": "From search results",
    };
    return (
      <>
        <span className="fact">
          {f.dobs.join(", ")} <Ids ids={f.dob_sources} />
        </span>
        <small>{f.dob_origin && origin[f.dob_origin]}</small>
        {f.dob_origin === "profile notes" && f.dob_quote && (
          <blockquote>
            <Rich>{f.dob_quote}</Rich>
          </blockquote>
        )}
      </>
    );
  }
  if (name === "location")
    return (
      <>
        {f.countries.length
          ? f.countries.map((c) => (
              <span className="fact" key={c}>
                <Rich>{c}</Rich>
              </span>
            ))
          : "None on the profile"}
      </>
    );
  if (name === "time")
    return f.earliest_report ? (
      <>
        Earliest dated report {f.earliest_report}
        <blockquote>
          <Rich>{f.earliest_report_line}</Rich>
        </blockquote>
      </>
    ) : (
      <>No dated report</>
    );
  return <>{f.death ? `Died ${f.death}` : "No date of death"}</>;
}
const labels = {
  type: "Type",
  name: "Name",
  dob: "Date of birth",
  location: "Country",
  time: "Age at earliest report",
  death: "Date of death",
};
const outcomes = {
  agree: "Agrees",
  contradict: "Contradicts",
  unknown: "Cannot compare",
};
export function ComparisonTable({
  hit,
  candidate,
  screenedAt,
}: {
  hit: Hit;
  candidate: Candidate;
  screenedAt: string;
}) {
  const decisive = new Set(
    candidate.reason_codes.flatMap((code) => CODE_CHECK[code] || []),
  );
  const checks = candidate.checks.filter(
    (c) =>
      !(c.name === "death" && !hit.trace?.hit.death) &&
      !(c.name === "time" && !hit.trace?.hit.earliest_report),
  );
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label="Customer and watchlist comparison"
    >
      <table className="comparison">
        <thead>
          <tr>
            {["Field", "Customer", "Watchlist profile", "Result"].map((x) => (
              <th scope="col" key={x}>
                {x}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {checks.map((check) => {
            const decided =
              decisive.has(check.name) &&
              (candidate.decision === "SAME_ENTITY"
                ? check.outcome === "agree"
                : check.outcome === "contradict");
            const label =
              candidate.customer.entity_type === "Company" &&
              check.name === "dob"
                ? "Incorporation date"
                : candidate.customer.entity_type === "Company" &&
                    check.name === "time"
                  ? "Existed at earliest report"
                  : labels[check.name];
            return (
              <tr key={check.name} className={decided ? "decided" : ""}>
                <th scope="row">
                  {label}
                  {decided && (
                    <span className="decided-tag">Decided this hit</span>
                  )}
                </th>
                <td>
                  <CustomerCell
                    name={check.name}
                    customer={candidate.customer}
                    screenedAt={screenedAt}
                  />
                </td>
                <td>
                  <HitCell name={check.name} hit={hit} />
                </td>
                <td>
                  <strong>{outcomes[check.outcome]}</strong>
                  <small>
                    <Rich>{check.detail}</Rich>
                  </small>
                  <Ids ids={check.sources} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!checks.length && (
        <p className="empty">No checks were returned for this candidate.</p>
      )}
    </div>
  );
}
