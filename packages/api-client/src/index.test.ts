import { expect, test } from "vitest";

import { SseParser, createAtlasClient } from "./index";

test("package exposes the generated client and SSE module", () => {
  expect(typeof createAtlasClient).toBe("function");
  expect(new SseParser().feed("")).toEqual([]);
});
