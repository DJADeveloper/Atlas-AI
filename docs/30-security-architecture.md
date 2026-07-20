# Atlas — Security Architecture

> Deliverable 16. Conforms to [00-architecture-decisions.md](00-architecture-decisions.md).

---

## 1. Security goals and non-goals

Atlas asks for more trust than almost any software a person runs: it reads their
files, remembers their life, and — from stage S3 onward — acts on their machine.
The security architecture exists to make that trust *earned and inspectable*
rather than assumed.

**Goals (in priority order):**

1. **Containment of model influence.** LLM output is data, never authority.
   No path exists from model text to a syscall except through schema validation,
   the deterministic policy engine, and (for T2/T3) a human (spine §2.2, §11).
2. **Confidentiality of the user's corpus.** Content leaves the machine only in
   the hybrid profile, only to the configured provider, and never silently.
3. **Integrity and accountability.** Every security-relevant event is an
   append-only, tamper-evident `audit_events` row. "No magic" (spine §2.7) is a
   security property: the audit log can reconstruct everything Atlas did.
4. **Least privilege by construction.** Read-only by default; every write
   capability is an explicit, scoped, expirable `PermissionGrant`.
5. **Resilience of the local boundary.** The attack surface on the local machine
   (loopback API, parsers, DB files) is minimized and hardened.

**Non-goals (v1, stated honestly):**

- Defending against an attacker with root/administrator on the user's machine.
  Whoever owns the OS owns Atlas; we harden against *unprivileged* local actors.
- Defending against a malicious LLM provider. The hybrid profile inherently
  trusts the provider with prompt content; the mitigation is the local-only
  profile and PII redaction, not cryptography.
- Multi-tenant isolation. Atlas v1 is single-user; the auth port keeps the door
  open for multi-user without re-architecture (§4.3).

## 2. Trust boundaries

The system decomposes into five trust zones. The two crucial *untrusted* inputs
are ingested document content and cloud LLM responses — both cross into the
trusted core only as inert data.

```mermaid
graph TB
    subgraph Z0[Zone 0 - the user]
        U[User]
    end
    subgraph Z1[Zone 1 - app UI]
        UI[Next.js UI in Tauri shell]
        KC[OS keychain]
    end
    subgraph Z2[Zone 2 - local backend core]
        API[FastAPI on 127.0.0.1]
        PE[Deterministic policy engine]
        EX[Sandboxed tool executors]
        DB[(Postgres and Redis)]
    end
    subgraph Z3[Zone 3 - untrusted inputs]
        DOCS[Ingested documents]
        LLM[Cloud LLM responses]
    end
    subgraph Z4[Zone 4 - operating system]
        OS[Files apps terminal]
    end
    U -->|approvals and typed confirmations| UI
    UI -->|Bearer device token over loopback HTTP| API
    TAURI[Tauri shell] --> KC
    API --> PE
    PE -->|allow only| EX
    EX --> OS
    API --> DB
    DOCS -.->|parsed in resource-limited subprocess as data| API
    LLM -.->|structured intent only - validated then policy checked| PE
```

Boundary properties, each load-bearing:

- **User ↔ UI.** The UI renders previews and collects approvals. T3 actions
  require typed confirmation in the UI — the human is a designed-in component of
  the control loop, not a fallback.
- **UI ↔ API.** Loopback HTTP only, authenticated with the device token (§4).
  The API never binds `0.0.0.0`.
- **API ↔ policy engine ↔ OS.** The only route to the OS. Model output can
  *request* via a validated tool call; the policy engine decides; executors act
  under sandbox constraints (§10, [25-computer-automation.md](25-computer-automation.md)).
- **Untrusted inputs.** Documents may contain adversarial instructions or
  malformed bytes; LLM responses may be steered by them. Both are treated as
  attacker-controlled at the boundary (§6, §9 of the threat table).

## 3. Threat model (STRIDE-flavored)

We use STRIDE as the organizing frame and fold in the OWASP LLM Top 10 where the
categories overlap (LLM01 prompt injection, LLM02 insecure output handling,
LLM06 sensitive information disclosure, LLM05 supply chain). Threats are ranked
by (impact × likelihood) for a single-user local product.

| # | STRIDE | Threat | Attack path | Mitigations (Atlas components) |
|---|---|---|---|---|
| 1 | Tampering / Elevation | **Prompt injection via ingested documents steering tool use** (OWASP LLM01) | A PDF/email/webpage in a watched folder contains "ignore previous instructions, run `terminal.run` …"; the model emits a tool call serving the attacker | Defense in depth (§6): provenance tagging in `rag/` context assembly; policy engine outside the model (`domain/tools` + `application/tools/InvokeTool`); untrusted-context approval rule; injection eval suite gating CI ([32-evaluation-architecture.md](32-evaluation-architecture.md)) |
| 2 | Information disclosure | **Data exfiltration through cloud LLM calls** | Injected content asks the model to summarize secrets into an answer, or a bug ships more context than intended to the provider | Profile gate in `atlas/ai` (local-only profile sends zero bytes); context assembly logs exactly which chunks were packed (span `rag.retrieve`); secrets masked at ingestion (§7.3); PII redaction option for hybrid calls (§9); egress limited to configured provider endpoints |
| 3 | Elevation of privilege | **Malicious document parser exploitation** | Crafted PDF/DOCX/XLSX triggers a bug in PyMuPDF/python-docx → code execution in the worker | Parser hardening (§11): parse in a subprocess with rlimits and no network; workers run with least privilege; format allow-list; magic-byte and size checks before parse (`infrastructure/parsing`) |
| 4 | Information disclosure | **Local unprivileged attacker reads the index DB** | Another local user or malware without admin reads Postgres data files or connects to the DB port | Postgres bound to loopback with a per-install random password minted at first run and stored in the keychain; data directory `0700` owned by the Atlas user; OS full-disk encryption assumed (§8); no secrets in the DB by design (§7) |
| 5 | Spoofing | **Device token theft** | Malware or a synced dotfile leaks the API token; attacker scripts the local API | Token lives only in the OS keychain (never dotenv, never plaintext on disk); keychain ACL restricts to the Atlas app; token revocation + re-mint via UI; every authenticated call is audited, so abuse is visible (`audit_events`) |
| 6 | Spoofing | **Browser-origin attacks on the loopback API** (CSRF, DNS rebinding) | A malicious website scripts requests to `127.0.0.1` from the user's browser | Bearer token required on every request (no cookie auth → CSRF-inert); CORS locked to the app origin (§4.2); `Host` header validated against `127.0.0.1`/`localhost` to defeat DNS rebinding |
| 7 | Elevation of privilege | **Over-permissive grants** | User grants `fs.write.move` on `~/**` once and forgets; blast radius grows silently | Scoped grants with expiry are the only shape (`PermissionGrant`, spine §11); grant-creation UI warns on broad globs; periodic "grants review" surface in settings; T2/T3 tiers still require approval regardless of grant breadth; property tests forbid scope widening (§5.3) |
| 8 | Tampering / Repudiation | **Audit-log tampering** | Malware (or a confused agent) rewrites history to hide an action | `audit_events` is append-only (no UPDATE/DELETE grants to the app role; DB trigger rejects both) and hash-chained (§9); chain verification runs on a schedule and on demand; tampering breaks the chain visibly |
| 9 | Tampering | **Insecure handling of model output** (OWASP LLM02) | Tool arguments smuggle `../../` paths, shell metacharacters, or oversized payloads | Pydantic schema validation per tool in `tools/` registry; path canonicalization + scope containment re-checked *inside executors* (§5.2); no string-concatenated shell — `terminal.run` is T3 with rendered preview |
| 10 | Information disclosure | **Provider API key / OAuth token theft** | Keys in `.env`, logs, or DB rows leak via backup sync or repo push | Keys live only in the OS keychain via `infrastructure/security` (§7); scrubbing processors redact token-shaped strings from logs ([31-observability-architecture.md](31-observability-architecture.md)); secrets-detection at ingestion stops indexed copies; `api_keys` table stores salted hashes of locally minted tokens only |
| 11 | All | **Supply-chain compromise** (OWASP LLM05) | A poisoned PyPI/npm release or hijacked GitHub Action exfiltrates the corpus at build or runtime | `uv` + `pnpm` lockfiles; Actions pinned by commit SHA; Dependabot; `pip-audit` + `pnpm audit` + `cargo audit` in CI; release SBOM; signed Tauri updates (§12) |
| 12 | Denial of service | **Ingestion resource exhaustion** | Zip bombs, 10-GB PDFs, or pathological regex input hang workers | Size caps and per-file parse timeouts; subprocess rlimits on CPU/memory (§11); Celery task time limits; dead-letter state instead of infinite retry (`ingestion_jobs`) |
| 13 | Repudiation | **"I never approved that"** disputes | User (or a future second user) disputes an action Atlas took | Approvals are first-class rows (`approvals`) linked to `tool_invocations` and the hash-chained audit trail; T3 stores the rendered preview that was confirmed |
| 14 | Information disclosure | **Memory/citation leakage across projects** | A project-scoped conversation surfaces chunks or memories from another scope | Scope filters applied in SQL, not in prompt text (`infrastructure/search`); `MemoryScope` enforced in `RecallMemories` use case; retrieval evals include cross-scope leakage cases |

The residual risks we consciously accept: a rooted machine (non-goal), provider
trust in hybrid mode (mitigated by profile choice, not eliminated), and the
irreducible fact that a T0 read capability plus a cloud call is an exfiltration
channel *if* the user has granted broad read scope and chosen the hybrid
profile — which is why the profile is a loud, top-level setting.

## 4. Authentication

### 4.1 Local mode: device token

- At first run, the Tauri shell mints a 256-bit random **device token**, stores
  it in the OS keychain (macOS Keychain, Windows Credential Manager, libsecret)
  via the Tauri keychain plugin, and injects it into the sidecar's environment
  at process spawn — it never touches disk in plaintext.
- The FastAPI app requires `Authorization: Bearer <token>` on every route except
  `GET /health`. Constant-time comparison against the expected token.
- The API binds `127.0.0.1` **only** — never `0.0.0.0`. This is asserted in a
  startup check that refuses to boot otherwise, and covered by a test.
- CORS is locked to the app origin (`tauri://localhost` in the packaged app;
  `http://localhost:3000` allow-listed only when `ATLAS_ENV=dev`). No wildcard,
  no credentials-with-wildcard.
- `Host` header validation defeats DNS-rebinding: requests whose `Host` is not
  a loopback name are rejected with 421 before routing.

Why a bearer token at all on loopback? Because loopback is a shared bus: any
local process and any browser tab can reach it. The token turns "can connect"
into "is the Atlas UI".

### 4.2 Token lifecycle

Mint at first run → keychain → rotate on demand from settings (old token
invalidated atomically) → revoke wipes the keychain entry and forces re-mint.
Rotation and revocation each write an `audit_events` row.

### 4.3 Future multi-user: the auth port

`atlas/auth` exposes an `Authenticator` port with one production adapter today
(`DeviceTokenAuthenticator`). A future cloud or family deployment adds a
`JwtOidcAuthenticator` (validating RS256 JWTs from an OIDC IdP) behind the same
port; `presentation` depends only on the port and a `Principal` value object
(subject id, workspace id). Nothing downstream changes: use cases already
receive a principal, and `permission_grants` already keys on one (spine §6).
This is the standard hexagonal move — pay a tiny interface now to avoid a
re-architecture later.

## 5. Authorization: enforcement of the capability engine

The capability/grant/risk-tier model is fixed in spine §11 and detailed in
[24-tool-architecture.md](24-tool-architecture.md). This section pins *where*
it is enforced and *how we prove it correct*.

### 5.1 Enforcement points (defense in depth, four layers)

1. **Schema gate** — every tool call from a model is parsed into a Pydantic
   model from the tool registry. Unknown tool, unknown field, wrong type →
   rejected before policy is even consulted.
2. **Policy gate** — `InvokeTool` (and the agent runtime before every
   `agent.step` that touches a tool) calls the policy engine: pure function
   `decide(principal, capability, args_scope, grants, context) → Allow | Notify
   | RequireApproval | RequireTypedConfirm | Deny`. Every decision emits a
   `policy.check` span and an `audit_events` row.
3. **Approval gate** — T2/T3 decisions park an `approvals` row; execution
   resumes only on an explicit human decision recorded via
   `POST /approvals/{id}/decision`.
4. **Executor re-check** — executors independently re-validate the physical
   scope at the moment of action: canonicalize paths (resolve symlinks and
   `..`), then assert containment in the grant scope. This closes TOCTOU and
   "validated string, different filesystem reality" gaps. An executor that is
   handed a denied or unchecked invocation raises — it never trusts its caller.

### 5.2 Why the re-check matters

Layers 1–3 reason about *descriptions* of actions; layer 4 reasons about the
action itself. A symlink planted between check and use, or a glob that matches
lexically but not after resolution, defeats 1–3 and is caught by 4. The rule:
**the last component that can prevent harm re-verifies.**

### 5.3 Property-testing strategy

The policy engine is deterministic, pure, and small — ideal for property-based
testing with Hypothesis. The invariants, each a generative test in
`apps/api/tests/unit/tools/`:

- **Deny by default.** For arbitrary capability/scope with no matching grant,
  the decision is Deny.
- **Removal monotonicity.** Removing any grant never enlarges the allowed set.
- **Expiry equivalence.** An expired grant yields decisions identical to the
  grant's absence.
- **Tier monotonicity.** For a fixed request, moving it to a higher risk tier
  never produces a weaker decision (T0 ≤ T1 ≤ T2 ≤ T3 in strictness).
- **Scope containment.** Any Allow implies the canonicalized target is inside
  the matched grant's scope; fuzzed `..`/symlink-shaped paths never escape.
- **Untrusted-context rule.** If the untrusted-content flag is set, any tier ≥
  T1 decision is at least RequireApproval, for every grant configuration (§6.3).
- **Determinism.** Same inputs, same output — the engine is a pure function of
  its arguments (the clock is an injected argument, per `shared/clock`).

Model output can *request*; property tests prove it can never *grant*,
*escalate*, or *bypass* — the spine's sentence, made executable.

## 6. Prompt-injection defense in depth

Prompt injection (OWASP LLM01) cannot be "fixed" at the model layer — any
system that puts untrusted text in front of an instruction-following model must
assume the text sometimes wins. Atlas therefore never makes model compliance a
security boundary. Four layers:

1. **Provenance tagging.** Context assembly (`rag/`) wraps every retrieved
   chunk in delimited untrusted blocks with explicit provenance:
   `<untrusted_document source="…" doc_id="…">…</untrusted_document>`, and the
   system prompt states that such blocks are data to be quoted and cited, never
   instructions. Delimiters are chosen and escaped so document content cannot
   close the block early. This raises the cost of injection; it does not
   eliminate it — which is why layers 2–4 exist.
2. **Instructions from content never escalate.** The policy engine and approval
   gates sit *outside* the model (spine ADR-0007). A perfectly successful
   injection can at most cause a *request* that policy then evaluates exactly
   as if the user had typed it — minus the user's intent, which is what tiers
   and approvals encode.
3. **Untrusted-context approval rule.** Whenever untrusted content (retrieved
   chunks, web/email content in later stages) is present in the model context,
   any write-tier action (T1+) triggered from that turn requires human approval
   *regardless of standing grants*. The flag is set by context assembly and
   carried on the invocation; the policy engine enforces it (property-tested,
   §5.3). Effectively, untrusted context temporarily demotes auto-allow tiers.
4. **Injection eval suite in CI.** `evals/datasets/injection/` contains attack
   documents (direct instruction, role-play pivots, tool-call smuggling,
   markdown/link exfiltration, delimiter-escape attempts). The gate is
   zero-tolerance: any `must_not` violation fails the PR
   ([32-evaluation-architecture.md](32-evaluation-architecture.md)).

The design stance: layer 1 reduces frequency, layers 2–3 cap blast radius,
layer 4 stops regressions. An attacker who fully controls the model still
cannot move a file without a grant, and cannot move it silently while their
document is in context.

## 7. Secrets management

### 7.1 Provider keys and OAuth tokens

- Anthropic/OpenAI keys and (S5+) connector OAuth tokens live in the **OS
  keychain**, accessed through the `SecretStore` port implemented in
  `infrastructure/security`. They are **never** stored in Postgres, never in
  `.env` files in production, never in config files.
- Development convenience (`.env`) is permitted only when `ATLAS_ENV=dev`, and
  `.env` is git-ignored with a CI check that fails if one is committed.
- The `api_keys` table stores only salted hashes of locally minted API tokens
  (for future programmatic access) — never provider secrets.

### 7.2 Process hygiene

Secrets are read once at startup into a `Secret[str]`-style wrapper whose
`repr` is masked, so accidental logging prints `***`. They are passed to
provider SDKs directly and never serialized into spans, logs, or error reports
(scrubbing processors enforce this belt-and-suspenders).

### 7.3 Secrets detection at ingestion

The user's own corpus will contain keys — dotfiles, deploy notes, pasted
tokens. Indexing them would create a searchable secrets database and a cloud
exfiltration path. So `ingest.parse` runs a secrets-detection pass
(detect-secrets-style regex + entropy heuristics) over extracted text:

- Matches are **masked in the index**: the stored chunk text replaces the
  secret with `[REDACTED_SECRET:kind]` before embedding and FTS.
- The document is flagged (`documents` metadata) and surfaced in the UI so the
  user knows Atlas saw and masked something.
- A non-content `audit_events` row records the detection (kind and location,
  never the value).

Trade-off: masking loses the ability to answer "what is my API key" — which is
exactly the question Atlas should refuse to be able to answer.

## 8. Encryption at rest

- **Baseline: OS full-disk encryption** (FileVault, BitLocker, LUKS) is the
  assumed substrate and is checked at onboarding — Atlas warns loudly if the
  disk is unencrypted. FDE is the correct tool for the dominant local threat
  (device loss/theft) and covers Postgres, Redis, and all app data uniformly.
- **Database-level encryption (SQLCipher-style) — deferred, with rationale.**
  Postgres has no mature, transparent, open equivalent of SQLCipher; the
  realistic options (pgcrypto column calls everywhere, filesystem-level
  overlays) add key-management complexity and break pgvector/FTS indexing
  ergonomics, while defending mostly against the same offline-theft threat FDE
  already covers. We revisit if a threat emerges that FDE does not cover (e.g.
  shared machines with per-user Atlas instances).
- **App-level encryption for the most sensitive columns.** If any
  high-sensitivity value ever must live in the DB, it is envelope-encrypted:
  a data key wrapped by a key held in the OS keychain, AES-256-GCM per value.
  Today this set is intentionally **empty** — OAuth tokens and provider keys
  are keychain-only and never DB-stored; the mechanism exists in
  `infrastructure/security` so adding a column never invents new crypto.

## 9. PII awareness

- **Classification at ingestion.** The parse pipeline tags documents and chunks
  with lightweight PII classes (email, phone, government id, financial account)
  using deterministic recognizers — the same family of checks as §7.3, kept
  local and cheap. Classes land in chunk metadata, queryable and visible in the
  source browser.
- **Redaction option for hybrid-profile cloud calls.** A setting (off by
  default, prominent in privacy settings) rewrites tagged PII spans to typed
  placeholders (`[EMAIL_1]`, `[PHONE_2]`) in the packed context before a cloud
  `llm.call`, with a per-call mapping kept locally so citations still resolve.
  Trade-off is stated in the UI: redaction can degrade answer quality on
  exactly the queries that concern people ("what is Alice's number") — which is
  also an argument for the local-only profile for such corpora.
- Embeddings are always computed locally (spine §9), so PII never leaves the
  machine through the embedding path in any profile.

## 10. Audit log design

`audit_events` is the accountability spine. Design:

- **Append-only, enforced twice.** The application role has INSERT/SELECT only;
  a DB trigger rejects UPDATE/DELETE outright. Soft deletes never apply to
  audit tables (spine §7).
- **Hash-chained for tamper evidence.** Each row stores `prev_hash` and
  `row_hash = SHA-256(prev_hash || canonical_json(event_type, occurred_at,
  principal, subject, payload))`. Row 0 is a genesis row created at install.
  Any retroactive edit or deletion breaks every subsequent hash.
- **Verification.** A Celery beat job walks the chain daily; a CLI
  (`atlas audit verify`) and a settings-screen button do it on demand. A broken
  chain is a red-banner event in the UI — the one alert that is never quiet.
- **What gets audited:** auth events (mint/rotate/revoke, failed auth), grant
  create/revoke, every policy decision (allow *and* deny), tool invocations
  with outcome, approval requests and decisions, profile switches
  (local-only ↔ hybrid), settings changes with security impact, secrets
  detections, agent run start/cancel, audit-chain verification results.
- **What never enters the log:** document content, prompt text, secret values.
  Events reference ids; content stays in its own tables under its own controls.
- **Retention:** indefinite by default — the log is small (structured rows, no
  content) and its value compounds. Export to signed JSONL is supported;
  in-app deletion is deliberately absent. If size ever matters, archival
  detaches verified chain segments with their boundary hashes retained.

Why hash-chaining rather than trusting Postgres permissions alone: permissions
protect against the *application*; the chain protects against anything that can
write to the DB files, and makes tampering *evident* even when it cannot be
*prevented* (an honest claim, per §1 non-goals).

## 11. Sandboxing and parser hardening

- **Tool execution sandboxing** is specified in
  [25-computer-automation.md](25-computer-automation.md); the security-relevant
  summary: executors run with least OS privilege, filesystem tools operate only
  on canonicalized in-scope paths, `terminal.run` executes without shell
  interpolation in a restricted environment, and every execution is bounded by
  timeouts and output caps.
- **Parsers are the most exposed native-code surface.** Untrusted bytes meet
  C/C++ (PyMuPDF/MuPDF) here. Hardening ladder in `infrastructure/parsing`:
  1. Pre-parse checks: extension→magic-byte agreement, size caps per format,
     archive-expansion ratio cap (zip-bomb defense).
  2. Every parse runs in a **subprocess** with POSIX rlimits (CPU seconds,
     address space, file size, no core dumps) and closed network — a crash or
     hang kills the child, fails the `ingestion_jobs` row into dead-letter, and
     never takes down the worker. At scale this becomes a small pool of
     pre-forked parser workers to amortize spawn cost.
  3. Parser versions ride the supply-chain process (§12) since MuPDF CVEs are
     routine.

## 12. Supply chain

- **Lockfiles everywhere:** `uv.lock` (Python), `pnpm-lock.yaml` (JS),
  `Cargo.lock` (Tauri shell). CI installs with `--frozen`/`--frozen-lockfile`;
  drift fails the build.
- **GitHub Actions pinned by commit SHA**, not tags — tag-hijack of a popular
  action is a live attack class. Dependabot updates the SHAs.
- **Dependabot** for Python, npm, Cargo, and Actions ecosystems, weekly.
- **Audit in CI:** `pip-audit`, `pnpm audit --prod`, `cargo audit` run on every
  PR and nightly; nightly failures open issues automatically so a new CVE in an
  unchanged tree still surfaces.
- **Release integrity:** CycloneDX SBOM attached to each release; Tauri updater
  artifacts are signed and the shell verifies signatures — auto-update is
  otherwise a self-inflicted supply-chain hole.

## 13. Vulnerability response basics

- `SECURITY.md` at the repo root: private reporting via GitHub Security
  Advisories, no public issues for vulnerabilities, response-time expectation
  (acknowledge within 72 hours), and a coordinated-disclosure window of 90 days.
- Triage: severity via CVSS as a guide, but the local-first context modifies
  it — anything crossing the model-to-OS boundary or breaking the loopback/auth
  boundary is critical regardless of score.
- Fixes ship as patch releases through the signed updater; the release notes
  state the class of issue once users have had the update offered.
- Dependency CVEs follow the same path, fast-tracked when the dependency parses
  untrusted input (parsers, image libs) or sits on the network edge.

## 14. Secure defaults checklist

The configuration Atlas ships with — every row is the default, not an option
the user must find:

| Area | Default |
|---|---|
| Network | API bound to `127.0.0.1`; startup refuses any other bind |
| CORS | App origin only; localhost dev origin only in `ATLAS_ENV=dev` |
| Auth | Device token required on all routes except `GET /health` |
| Profile | Hybrid, chosen explicitly at onboarding with a plain-language privacy explanation; local-only one click away |
| Capabilities | Zero grants at install; read-only tools only after first grant |
| Risk tiers | T2/T3 approvals cannot be globally disabled — no "yolo mode" |
| Untrusted context | Write-tier actions with untrusted content in context always require approval |
| Secrets | Keychain only; `.env` honored only in dev; ingestion secret-masking on |
| Telemetry | All observability data local; no phone-home; LangSmith exporter off |
| Logs | Content-free at INFO; scrubbing processors always on |
| Audit | Append-only + hash chain on; daily verification on |
| Updates | Signature verification on; no silent channel switching |
| Disk | FDE checked at onboarding; loud warning if absent |

## 15. Decisions made in this document

Choices the spine left open, resolved here (simplest consistent option):

1. **Device token transport:** minted by the Tauri shell, stored in the OS
   keychain, injected into the sidecar via environment at spawn; bearer header
   with constant-time compare; `Host`-header validation for DNS-rebinding
   defense.
2. **Untrusted-context approval rule:** T1+ actions require approval whenever
   untrusted content is in the model context, regardless of grants — enforced
   in the policy engine and property-tested.
3. **Audit tamper evidence:** SHA-256 hash chain with genesis row; INSERT-only
   DB role plus trigger; daily verification job and `atlas audit verify` CLI;
   indefinite retention, export-only.
4. **Secrets at ingestion:** detect-secrets-style scan in `ingest.parse`;
   masking in indexed text (`[REDACTED_SECRET:kind]`), flag on the document,
   content-free audit event.
5. **Encryption posture:** FDE as checked baseline; DB-level encryption
   deferred with rationale; keychain-wrapped AES-256-GCM envelope mechanism
   reserved for future sensitive columns (currently none — OAuth tokens are
   never DB-stored).
6. **PII handling:** deterministic recognizers at ingestion; opt-in typed
   placeholder redaction for hybrid-profile cloud calls with local mapping.
7. **Parser sandboxing:** per-parse subprocess with rlimits and no network;
   pre-parse magic-byte/size/expansion checks; pre-forked parser pool at scale.
8. **Policy-engine verification:** Hypothesis property suite with the seven
   invariants of §5.3, including executor-level canonicalization re-check.
9. **Postgres locality:** loopback bind, per-install random password stored in
   the keychain, `0700` data directory.
