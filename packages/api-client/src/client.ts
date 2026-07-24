/**
 * The generated Atlas API client (ADR-0001).
 *
 * `paths` comes from `src/schema.d.ts`, generated from the backend's
 * OpenAPI spec by `pnpm gen`; CI regenerates and fails on any
 * uncommitted diff, so this package IS the API contract's enforcement.
 * All REST calls go through here — hand-rolled fetches against
 * /api/v1 are lint-banned in apps.
 */

import createClient from "openapi-fetch";

import type { paths } from "./schema";

export interface AtlasClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
}

export function createAtlasClient(options: AtlasClientOptions) {
  return createClient<paths>({
    baseUrl: options.baseUrl,
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });
}

export type AtlasClient = ReturnType<typeof createAtlasClient>;
export type { paths } from "./schema";
