import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AlertDetail, Hit } from "../api/types";
import details from "../api/fixtures/alert_details.json";
import { ComparisonTable } from "./ComparisonTable";

const all = Object.values(details) as unknown as AlertDetail[];
function hit(id: string): Hit {
  return structuredClone(
    all.flatMap((a) => a.hits).find((h) => h.hit_id === id)!,
  );
}
function table(h: Hit, screenedAt = "2026-11-16") {
  return render(
    <ComparisonTable
      hit={h}
      candidate={h.trace!.candidates[0]}
      screenedAt={screenedAt}
    />,
  );
}
describe("comparison table reference behavior", () => {
  it("highlights all decisive contradictions and shows source chips", () => {
    const { container } = table(hit("H-3205C2"));
    const decisive = container.querySelectorAll("tr.decided");
    expect(decisive).toHaveLength(2);
    expect(decisive[0]).toHaveTextContent("Name");
    expect(decisive[1]).toHaveTextContent("Country");
    expect(
      screen.getAllByRole("button", { name: "Open record KYC-A932A4" }).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Verified legal name")).toBeInTheDocument();
    expect(
      screen.queryByRole("rowheader", { name: /Age at earliest/ }),
    ).not.toBeInTheDocument();
  });
  it("does not call a REVIEW agreement or country contradiction decisive", () => {
    const { container } = table(hit("H-ADF453"));
    expect(container.querySelectorAll("tr.decided")).toHaveLength(0);
    expect(screen.getByText("1979, 1980")).toBeInTheDocument();
    expect(screen.getByText("Listed on the profile")).toBeInTheDocument();
  });
  it("highlights name, birth date and country agreements for a confirmed match", () => {
    const { container } = table(hit("H-0181BD"));
    expect(container.querySelectorAll("tr.decided")).toHaveLength(3);
    expect(screen.getByText("From search results")).toBeInTheDocument();
    expect(
      screen.getByRole("rowheader", { name: /Date of birth/ }).closest("tr"),
    ).toHaveTextContent("Agrees");
  });
  it("uses company labels and earliest report evidence", () => {
    table(hit("H-A3B158"));
    const timeRow = screen
      .getByRole("rowheader", { name: /Existed at earliest report/ })
      .closest("tr")!;
    expect(timeRow).toHaveClass("decided");
    expect(timeRow).toHaveTextContent("Earliest dated report");
    expect(
      screen.getByRole("rowheader", { name: "Incorporation date" }),
    ).toBeInTheDocument();
  });
  it("shows death against screening date and the decisive death check", () => {
    table(hit("H-BD709E"), "2026-11-14");
    const row = screen
      .getByRole("rowheader", { name: /Date of death/ })
      .closest("tr")!;
    expect(row).toHaveClass("decided");
    expect(within(row).getByText("Screened 2026-11-14")).toBeInTheDocument();
    expect(row).toHaveTextContent("Died");
  });
  it("deduplicates declared names, groups country sources and quotes profile notes", () => {
    const h = hit("H-ADF453");
    const c = h.trace!.candidates[0].customer;
    c.verified_name = null;
    c.declared_names = [
      { value: "Declared Person", source: "CUS-123" },
      { value: "Declared Person", source: "A-123" },
    ];
    c.countries = [
      { value: "Example country", source: "CUS-123" },
      { value: "Example country", source: "NOTE-123" },
    ];
    h.trace!.hit.dob_origin = "profile notes";
    h.trace!.hit.dob_quote = "Profile says born in 1979.";
    table(h);
    expect(screen.getAllByText("Declared Person")).toHaveLength(1);
    expect(screen.getByText("Declared, not verified")).toBeInTheDocument();
    expect(screen.getAllByText("Example country")).toHaveLength(1);
    expect(screen.getByText("Profile says born in 1979.").tagName).toBe(
      "BLOCKQUOTE",
    );
    expect(
      screen.getByRole("button", { name: "Open record NOTE-123" }),
    ).toBeInTheDocument();
  });
});
