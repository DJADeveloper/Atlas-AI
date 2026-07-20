# ADR-0006: Tauri + local FastAPI sidecar for desktop

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Atlas must ship as a desktop application with native access (filesystem watch,
keychain, tray, global shortcuts, notifications) around a rich web UI, while
the intelligence lives in a Python backend (FastAPI) that also serves dev and
future self-host/cloud modes unchanged.

## Decision

The desktop app is a **Tauri 2** shell embedding the Next.js UI, managing the
FastAPI backend as a **localhost sidecar process** (spawn, health-check, port
negotiation on 127.0.0.1, crash restart, shutdown). Native concerns (keychain,
tray, updater, OS permissions) live in the Rust shell; all product logic stays
in the sidecar + UI.

## Alternatives considered

- **Electron.** Mature, battle-tested, Node main process. Rejected: ~10× the
  memory/disk footprint of Tauri for a product whose pitch includes "runs
  quietly on your machine"; Chromium-per-app weight; and we'd still need the
  Python sidecar, so Electron buys us nothing Tauri doesn't except familiarity.
- **Pure Python desktop (PyWebview/PySide).** One runtime, no Rust. Rejected:
  weaker webview control, no first-class updater/signing story, worse security
  posture (Tauri's allowlisted IPC and tiny TCB are genuinely better), and the
  UI would diverge from the web dashboard build.
- **Rewriting the backend in Rust/TS to avoid a sidecar.** Eliminates process
  management, but abandons the Python AI ecosystem (PyMuPDF, pandas, provider
  SDKs, tree-sitter bindings) that the entire ingestion layer stands on.
  Rejected without much agonizing.

## Consequences

- One backend codebase serves dev (compose), desktop (sidecar), self-host, and
  cloud — deployment targets differ only in packaging
  (`40-deployment-architecture.md`).
- We own sidecar lifecycle engineering: startup ordering, port conflicts,
  crash-looping, log capture, clean shutdown — M14 treats this as first-class
  scope with tests, because "the app sometimes starts broken" is a product
  killer.
- Python packaging for the sidecar (and a bundled Postgres) is the hardest
  packaging problem in the project; the deployment doc owns the approach and
  its risks are tracked in `51-risk-analysis.md`.
- API remains loopback-only with token auth even though it's local — defense
  in depth against other local processes (`30-security-architecture.md`).

## Revisit triggers

Tauri 2 ecosystem instability blocking a release-critical need (updater,
signing); sidecar packaging failure rates that resist engineering effort;
a future where the backend is primarily remote for most users.
