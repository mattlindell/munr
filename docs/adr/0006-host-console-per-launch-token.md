# ADR 0006 — The Console runs on the host and gates mutations with a per-launch token

**Status:** Accepted — 2026-11-20

## Context

The Console is the only surface allowed to promote a Pending Fact to Confirmed (ADR 0002). The CLI
is deliberately read-and-propose-only because an agent invokes it, so it can't be trusted with
`user` provenance. Two problems surfaced while triaging PV-4:

1. **Placement.** The donor web UI is served by the `mcp-server` container. That container talks to
   the donor `memory` database over `psycopg2` and doesn't ship the `munr` package. Facts live in
   the dedicated pg17 instance (ADR 0005), and every Fact mutation regenerates the desktop Snapshot
   (ADR 0003). A Console inside a container would write the Snapshot to the container's
   filesystem. The Injection Hook would then not see the confirmation until the TTL backstop
   fired. That breaks the event-triggered invalidation contract.
2. **Trust boundary.** An unauthenticated `POST /api/facts/.../confirm` on localhost lets any
   agent on the desktop confirm a Fact with `curl`. That brings back the exact model discretion
   the confirmed-only write path removes. It would also make the CLI's restrictions pointless.

## Decision

**The Console runs on the host** as a thin HTTP process built on the `munr` core (`FactStore`). It
shares the same database config and Snapshot path as the CLI and the hook, so a confirmation is
injected on the very next turn. It binds `127.0.0.1` only. It doesn't import the heavy stack
(`torch`, `sentence-transformers`, `psycopg2`).

**Fact mutations require a per-launch token held in the browser:**

- The Console generates a fresh random token at each launch. The token is kept in memory only and
  never written to disk.
- The token reaches the browser only through the launch URL the Console prints or opens. It
  follows the Jupyter pattern: `http://127.0.0.1:<port>/ui?token=…`. The browser keeps the token
  in session storage and drops it from the address bar.
- Every mutating request (`confirm`, `reject`, and later PV-21's direct user write) must send the
  token. A missing or wrong token is rejected with `403` and doesn't mutate anything.
- The served HTML and every unauthenticated endpoint must never contain the token. A plain `curl`
  of `/ui` therefore doesn't return it.

Facts are addressed by their full identity `(scope, key)` on every route. The global scope `*` is
URL-encoded.

## Consequences

- An agent can't confirm a Fact with a bare HTTP call. It would have to scrape the Console's launch
  output, which is a deliberate act and not an accident of shell access.
- Remaining risk, accepted for 0.5: an agent that launches the Console itself, or reads its
  stdout, can get the token. Users should launch the Console themselves, outside agent sessions.
- Restarting the Console invalidates open browser sessions. That's acceptable for a local review
  tool.
- PV-21's direct-user write path inherits this guard instead of designing its own auth.
- The donor container's memory endpoints aren't served by the host Console. The Console's
  memory sections are empty in 0.5, which exercises only the `facts` table (ADR 0005), until PV-5
  and the episodic tier decide where those endpoints live.
