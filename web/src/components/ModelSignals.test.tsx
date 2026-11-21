import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AlertDetail } from "../api/types";
import details from "../api/fixtures/alert_details.json";
import { ModelSignals } from "../pages/Case";
const hit = () =>
  structuredClone(
    (details["A-0DF328"] as unknown as AlertDetail).hits.find(
      (h) => h.hit_id === "H-ADF453",
    )!,
  );
describe("advisory availability", () => {
  it("shows honest disconnected states without inventing scores", () => {
    const h = hit();
    h.ml = null;
    h.llm = null;
    render(<ModelSignals hit={h} />);
    expect(
      screen.getByText("ML not connected for this hit."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("LLM second opinion not connected for this hit."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });
  it("distinguishes unscored values and calls out near misses and disagreement", () => {
    const h = hit();
    h.ml!.priority = null;
    h.ml!.name_similarity = null;
    h.ml!.anomaly = null;
    h.ml!.near_miss = true;
    h.llm!.second_opinion!.agrees = false;
    h.llm!.second_opinion!.decision = "SAME_ENTITY";
    render(<ModelSignals hit={h} />);
    expect(screen.getByText("Not scored for this hit")).toBeInTheDocument();
    expect(screen.getAllByText("Not scored")).toHaveLength(2);
    expect(screen.getByText(/Near miss:/)).toBeInTheDocument();
    expect(screen.getByText("Disagrees with engine")).toBeInTheDocument();
  });
});
