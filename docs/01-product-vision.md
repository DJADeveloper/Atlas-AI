# Atlas — Product Vision

> Deliverable 1. Conforms to [00-architecture-decisions.md](00-architecture-decisions.md).

---

## 1. What Atlas is

Atlas is an **AI Operating System**: a secure, local-first layer between a
person and their digital world. You talk to your computer — in text, and
eventually in voice — and Atlas understands what you have, finds what you mean,
explains what it knows, and (with your explicit permission) acts on your behalf.

Atlas is explicitly **not**:

- a chatbot with a knowledge cutoff and no knowledge of *you*,
- a RAG demo that indexes a folder once and bit-rots,
- a thin wrapper that forwards your files to someone else's cloud.

The one-sentence test for every feature: *does this make the computer feel like
a capable, trustworthy colleague who has read everything you've shared with it
and never acts without asking?*

## 2. The problem

Modern knowledge work is scattered across files, folders, repos, notes, mail,
calendars, and half a dozen SaaS silos. The operating system — the thing that is
supposed to organize computation for a human — has no idea what any of it
*means*. Search is lexical, organization is manual, and context lives only in
the user's head:

- "I know I wrote this down somewhere" costs minutes, many times a day.
- Cross-document questions ("what changed since last week's architecture?")
  have no tool at all — only manual re-reading.
- Routine digital chores (organizing downloads, starting a dev environment,
  prepping for tomorrow) are pure toil that computers should absorb.
- The tools that *do* understand content (cloud AI assistants) demand that you
  hand your entire private corpus to a third party, permanently.

## 3. The product bet

Three bets, in order of conviction:

1. **Grounded personal AI is a step-change, not an increment.** An assistant
   that answers *from your actual documents, with citations you can click* is
   categorically more useful than one that answers from the internet's average.
2. **Trust is the product.** People will let AI touch their filesystem exactly
   in proportion to how legible and revocable its permissions are. Read-only by
   default, explicit grants, previews, approvals, undo, and an audit trail are
   not compliance features — they are *the* adoption features.
3. **Local-first is a durable wedge.** Platform vendors will ship cloud-first
   assistants. A product where the index, the memories, and the telemetry stay
   on the user's machine — and cloud reasoning is a per-profile, visible choice —
   serves the users those vendors structurally cannot.

## 4. Who it's for

- **v1 persona: the technical knowledge worker.** Developers, architects,
  consultants, founders — people with large heterogeneous corpora (code, specs,
  proposals, meeting notes), high privacy sensitivity, and tolerance for a
  desktop app that asks for folder permissions. They are also the persona the
  founder embodies, which keeps feedback loops honest.
- **Later:** researchers, lawyers, and small teams (self-host server mode),
  then a broader prosumer audience once voice and connectors mature.

## 5. The experience, by autonomy stage

Each stage is a promise kept before the next is attempted (spine §1):

| Stage | The feeling | Example utterances that start working |
|---|---|---|
| **S1 Read-only knowledge** | "It has actually read my stuff." | "Find the proposal I wrote for CAIR." · "Summarize my meeting notes." · "Explain this code." |
| **S2 Project understanding** | "It connects things I forgot were connected." | "What changed since last week's architecture?" · "Open everything related to Melitta." · "What was I working on yesterday?" |
| **S3 Safe computer actions** | "It can do chores — and it always shows its work first." | "Organize my Downloads folder." · "Start my development environment." |
| **S4 Voice** | "I just talk to my computer." | Everything above, hands-free, with sub-second responsiveness. |
| **S5 Connected services** | "It sees my whole digital world, on my terms." | "Draft a response using the documents we discussed." · "Compare these contracts with what legal sent." |
| **S6 Autonomous workflows** | "It runs my routines." | "Prepare me for tomorrow." · "Generate tasks from today's meeting, every day." |

## 6. Product principles

1. **Grounded or silent.** Answers about the user's data carry citations or an
   honest "I don't know." Confidence is displayed, not implied.
2. **Nothing invisible.** Every action Atlas takes is visible in a timeline and
   reconstructable from the audit log. No hidden automation, ever.
3. **Permission is UX.** Grants are legible sentences ("Atlas may *read*
   `~/Projects`"), revocable in one click, and never expanded silently.
4. **Destructive means confirmed.** Anything irreversible shows a preview and
   requires explicit confirmation. Deletes go to the trash, moves are undoable.
5. **The user owns the brain.** Index, memories, preferences, and history live
   locally, exportable, deletable. Switching LLM providers never means losing
   your accumulated context.
6. **Fast enough to feel like the OS.** Search results in hundreds of
   milliseconds; first streamed token about a second. Slowness breaks the
   "operating system" illusion faster than any missing feature.

## 7. What success looks like

**As a product** — daily-driver retention: the user searches Atlas before
Finder/Spotlight, asks Atlas before opening files manually, and trusts S3
actions enough to use them weekly. Concrete v1 targets: p50 search < 300 ms on
a 100k-chunk corpus; grounded-answer citation precision ≥ 0.9 on the eval set;
zero unaudited actions; onboarding to first cited answer < 10 minutes.

**As a portfolio artifact** — the repository itself demonstrates Staff-level
applied-AI engineering: clean architecture with a real security boundary,
evaluation-driven development with CI regression gates, full observability with
cost attribution, and documentation that teaches its trade-offs. Every phase
ends in a demo a stranger can run (`50-feature-roadmap.md` lists them).

## 8. Why now

- Frontier models (Claude 5-class) are reliably good at tool selection and
  grounded synthesis — the two capabilities Atlas's architecture leans on.
- Local models and embeddings (Ollama-class) are good enough to keep the
  *entire* pipeline on-device for privacy-mode users — embeddings never leave
  the machine even in hybrid mode (spine §9).
- Desktop runtimes (Tauri 2) make a secure, small-footprint native shell around
  a web UI genuinely pleasant to ship.
- The ecosystem norm of "upload everything to us" has created a visible,
  underserved constituency for local-first AI.

## 9. Non-goals (v1)

No unrestricted computer control. No browser automation. No mobile app. No
model fine-tuning. No multi-agent swarms. No plugin marketplace. Each is either
a trust risk before the permission system has earned credibility, or scope that
would starve the core loop. `50-feature-roadmap.md` tracks what would unlock
reconsideration.

## 10. The long arc

S1–S2 build the **knowledge platform** (understanding). S3–S4 build the
**action platform** (trust). S5–S6 compose them into the **personal operating
layer**: Atlas plans your morning from your calendar and your documents, runs
your routines under budgets you set, and hands you the day pre-assembled —
"Prepare me for tomorrow" as an ordinary, boring, reliable feature. The moat at
that point is not the model (rented, swappable) but the accumulated, structured,
user-owned context — the knowledge index, the memory system, and the knowledge
graph — plus the earned trust of a permission system that never once surprised
its user.
