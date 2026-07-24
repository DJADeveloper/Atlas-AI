import { expect, test } from "vitest";

import { toneForState } from "./index";

test("state-to-tone vocabulary is total", () => {
  expect(toneForState("succeeded")).toBe("ok");
  expect(toneForState("running")).toBe("busy");
  expect(toneForState("failed")).toBe("error");
  expect(toneForState("anything-else")).toBe("muted");
});
