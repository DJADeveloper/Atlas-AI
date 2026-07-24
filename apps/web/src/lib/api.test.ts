import { expect, test } from "vitest";

import { API_BASE_URL } from "./api";

test("API base URL defaults to the local backend", () => {
  expect(API_BASE_URL).toBe("http://localhost:8000");
});
