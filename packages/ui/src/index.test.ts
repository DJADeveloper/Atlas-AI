import { expect, test } from "vitest";

import { PACKAGE_NAME } from "./index.js";

test("workspace package is wired into the test pipeline", () => {
  expect(PACKAGE_NAME).toBe("@atlas/ui");
});
