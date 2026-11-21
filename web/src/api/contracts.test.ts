import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";
describe("frozen contract copies", () => {
  it("keeps the TypeScript contract byte-identical", () => {
    expect(
      readFileSync("src/api/types.ts").equals(
        readFileSync("../contracts/types.ts"),
      ),
    ).toBe(true);
  });
  it("keeps every fixture byte-identical", () => {
    for (const file of readdirSync("../contracts/fixtures").filter((f) =>
      f.endsWith(".json"),
    )) {
      expect(
        readFileSync(`src/api/fixtures/${file}`).equals(
          readFileSync(`../contracts/fixtures/${file}`),
        ),
        file,
      ).toBe(true);
    }
  });
});
