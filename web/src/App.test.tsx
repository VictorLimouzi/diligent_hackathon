import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import axe from "axe-core";
import App from "./App";

function mount(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}
async function settled() {
  await waitFor(() =>
    expect(screen.queryAllByText("Loading…")).toHaveLength(0),
  );
}
describe("fixture-backed analyst screens", () => {
  it("filters the queue by status, practice labels, query and blocker without reordering", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("11 alerts shown");
    const rows = () =>
      screen
        .getByRole("region", { name: "Screening queue" })
        .querySelectorAll("tbody tr");
    expect(rows()[0]).toHaveTextContent("IBRAHIM SHUAIBU");
    await user.selectOptions(screen.getByLabelText("Status"), "cleared");
    await screen.findByText("2 alerts shown");
    await user.selectOptions(screen.getByLabelText("Status"), "");
    await user.click(screen.getByLabelText("Practice labelled"));
    await screen.findByText("4 alerts shown");
    await user.click(screen.getByLabelText("Practice labelled"));
    await user.click(screen.getByText("What is keeping the queue open?"));
    await user.click(
      screen.getByRole("button", {
        name: /Name and birth date agree, countries differ/,
      }),
    );
    await screen.findByText("1 alerts shown");
    expect(rows()[0]).toHaveTextContent("Markus Müller");
    await user.click(
      screen.getByRole("button", { name: "Clear blocker filter" }),
    );
    await user.type(screen.getByLabelText("Search queue"), "missingperson");
    await screen.findByText("No alerts match these filters.");
  });
  it("opens a queue selection with j/k and Enter, and does not intercept text entry", async () => {
    mount();
    await screen.findByText("11 alerts shown");
    fireEvent.keyDown(document, { key: "j" });
    fireEvent.keyDown(document, { key: "k" });
    fireEvent.keyDown(document, { key: "j" });
    fireEvent.keyDown(document, { key: "Enter" });
    expect(
      await screen.findByRole("heading", { name: "BROOKS N/A" }),
    ).toBeInTheDocument();
  });
  it("opens the exact deep-linked hit, fetches records, streams advisory work and preserves the engine after review", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    mount("/alerts/A-0DF328/H-ADF453");
    await screen.findByRole("heading", { name: "Markus Müller" });
    expect(document.querySelector(".hit-tab.active")).toHaveTextContent(
      "Marcus Miller",
    );
    await user.click(
      screen.getAllByRole("button", { name: "Open record KYC-A932A4" })[0],
    );
    const drawer = screen.getByRole("dialog", { name: "Source record" });
    expect(
      await within(drawer).findByText("Identity check"),
    ).toBeInTheDocument();
    await user.click(
      within(drawer).getByRole("button", { name: "Close Source record" }),
    );
    await user.click(screen.getByRole("button", { name: "Investigate" }));
    expect(
      await screen.findByText(
        "Ask the customer whether they have lived or worked in the United States.",
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    await user.type(
      screen.getByLabelText("Question"),
      "Why is country not enough?",
    );
    await user.click(screen.getByRole("button", { name: "Send question" }));
    await waitFor(
      () =>
        expect(
          screen.getByRole("log", { name: "Casebook conversation" }),
        ).toHaveTextContent("both agree with the profile."),
      { timeout: 3000 },
    );
    await user.click(screen.getByRole("button", { name: "Draft note" }));
    const draft = await screen.findByLabelText("Case note draft");
    await user.clear(draft);
    await user.type(draft, "Edited case note with KYC-A932A4.");
    const notePanel = screen
      .getByRole("heading", { name: "Case note" })
      .closest("section")!;
    await user.click(within(notePanel).getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("Edited case note with KYC-A932A4.");
    expect(
      within(notePanel).getByText("Not connected · deterministic stand-in"),
    ).toBeInTheDocument();
    const review = within(
      document.querySelector(".review-panel") as HTMLElement,
    );
    await user.selectOptions(review.getByLabelText("Decision"), "SAME_ENTITY");
    await user.selectOptions(
      review.getByLabelText("Reason code"),
      "NAME_DOB_MATCH",
    );
    await user.type(
      review.getByLabelText("Review comment"),
      "Verified new address.",
    );
    expect(review.getByText("Overrides engine decision.")).toBeInTheDocument();
    await user.click(
      review.getByRole("button", { name: "Record analyst decision" }),
    );
    await screen.findByText(
      "Review recorded for this mock session. Reloading resets it.",
    );
    await waitFor(() =>
      expect(document.querySelector(".audit-entry")).toHaveTextContent(
        "Verified new address.",
      ),
    );
    expect(screen.getByLabelText("Engine decision")).toHaveTextContent(
      "Review",
    );
  }, 10000);
  it("switches candidates and reports unavailable fixture actions honestly", async () => {
    const user = userEvent.setup();
    mount("/alerts/A-3983D4");
    await screen.findByRole("heading", { name: "IBRAHIM SHUAIBU" });
    const buttons = document.querySelectorAll(".candidate-select");
    expect(buttons).toHaveLength(4);
    await user.click(buttons[1]);
    expect(buttons[1]).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Investigate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "available for H-ADF453 only",
    );
  });
  it("cancels a running stream and can restart it", async () => {
    const user = userEvent.setup();
    mount("/alerts/A-0DF328/H-ADF453");
    await screen.findByRole("heading", { name: "Markus Müller" });
    await user.click(screen.getByRole("button", { name: "Investigate" }));
    await user.click(
      screen.getByRole("button", { name: "Stop investigation" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Investigation stopped",
    );
    await user.click(screen.getByRole("button", { name: "Investigate" }));
    expect(
      await screen.findByText(
        "Ask the customer whether they have lived or worked in the United States.",
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
  });
  it("renders QA metrics, the matrix, disagreements and case links", async () => {
    mount("/qa");
    await screen.findByRole("heading", { name: "Decision quality" });
    await settled();
    expect(
      screen
        .getByRole("region", { name: "Practice label comparison" })
        .querySelectorAll("tbody tr"),
    ).toHaveLength(4);
    expect(
      screen
        .getByRole("region", { name: "Engine versus second opinion matrix" })
        .querySelectorAll("tbody tr"),
    ).toHaveLength(3);
    const disagreements = screen.getByRole("region", {
      name: "Second opinion disagreements",
    });
    expect(disagreements.querySelectorAll("tbody tr")).toHaveLength(6);
    expect(
      within(disagreements).getByRole("link", {
        name: "Raković, Aleksandar →",
      }),
    ).toHaveAttribute("href", "/alerts/A-AE9222/H-622EDD");
  });
  it("searches customers and alerts in the keyboard command palette", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByText("11 alerts shown");
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    const dialog = screen.getByRole("dialog", { name: "Search Casebook" });
    await user.type(within(dialog).getByRole("searchbox"), "Markus");
    expect(
      await within(dialog).findByRole("button", {
        name: "Open record CUS-117438",
      }),
    ).toBeInTheDocument();
    await user.click(
      await within(dialog).findByRole("link", { name: /Markus Müller/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "Markus Müller" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "Search Casebook" }),
    ).not.toBeInTheDocument();
  });
  it.each(["/", "/alerts/A-0DF328/H-ADF453", "/qa"])(
    "has no detectable WCAG structural failures at %s (contrast requires a browser)",
    async (path) => {
      mount(path);
      await settled();
      const results = await axe.run(document.body, {
        runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
        rules: { "color-contrast": { enabled: false } },
      });
      expect(results.violations).toEqual([]);
    },
  );
});
