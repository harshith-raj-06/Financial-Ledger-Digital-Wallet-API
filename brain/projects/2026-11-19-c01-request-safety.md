---
title: "C01 HTTP request boundaries"
author: "P. Jaya Raghunandhan Reddy (C), AI-assisted; human review pending"
date: "2026-11-19"
category: "projects"
type: "ai-session"
tags: ["member-c", "http", "validation", "progress"]
requirements: ["EI-SW-1", "NFR-SEC-3", "NFR-SEC-7", "NFR-REL-2", "SB-5", "AI-3"]
source: "Final 14-page SRS section 3.2 and 5.3; revised distribution C01; older SRS NFR-OBS-1 and body-limit detail"
---

# C01 HTTP request boundaries

## Scope and design

Continues [[2026-11-19-c01-cors]] on raghu/api-request-safety.
[PR #11](https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API/pull/11)
targets the pending CORS branch. Git transport still aborts and the integration
rejects writes, so publication uses signed web-editor commits with descriptions.
The submitted SRS
defines structured errors, safe validation and no secret logging. The revised
C01 package also requires request IDs and body-size validation; the older SRS
supplies the 16 KiB value and observability detail. Older requirement numbering
is not substituted for the final SRS's NFR-SEC-7 safe-error requirement.

Pure ASGI middleware buffers POST bodies only up to 16 KiB before routing. It
checks actual streamed bytes, not only Content-Length, and replays accepted
bytes to the handler. Invalid length headers fail safely. The error boundary
converts unexpected failures before response headers into sanitized errors.

Middleware order: request context -> CORS -> request safety -> routing.
Thus CORS also covers 413/500 responses, and preflight responses also get IDs.
Server-generated IDs replace caller input. Logs use route templates and omit
headers, payloads, query values, unknown path values and exception messages.
Streaming failures after response headers cannot be replaced with a new JSON
response; the exception is sanitized before propagation. Product endpoints
currently return ordinary JSON, not streaming responses.

The API factory registers common handlers for validation, framework errors and
ApiError. Per-feature field validation, authentication, provider integration and
financial operations retain their designated owners. No test probe is exposed
by the production factory.

## Validation and integration

- Local Python 3.14: 80 tests passed, including 36 new request-boundary cases
  and all 44 existing health/CORS/readiness/migration cases.
- Boundary, oversized, chunked, malformed and duplicate-length cases covered.
- Error codes, generic internal responses, method headers and CORS covered.
- Twenty concurrent calls received different UUID v4 IDs.
- Logging tests verified one structured record and no supplied secret values.
- Main remains at 0004893; its existing tests are part of the regression suite.
- git merge-tree passed against current main 0004893 and the CORS base, with
  no conflicts. The check does not guarantee future merges remain conflict-free.
- Both GitHub workflows passed at 4fc0ca5: Python 3.11 API tests, lint, build,
  team tests and secret scanning. Evidence: [API checks](https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API/actions/runs/44535050277)
  and [team CI](https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API/actions/runs/44535050281).
  Final documentation commits trigger another run before review handoff.

## AI session and next packages

No teammate feature, main branch, approval or merge is modified by this work.
This is development evidence, not one of C10's required authored research notes.
After publication, continue C02 OpenAPI/Prism and C03 interfaces/fakes. C04/C05
integration requires A's real schema/provider contract; C06-C08 and C10 have
their own dependencies listed in docs/member-c-progress.md.

References: [FastAPI error handlers](https://fastapi.tiangolo.com/tutorial/handling-errors/)
and [Starlette middleware](https://www.starlette.io/middleware/).
