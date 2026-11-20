# C01: Restrict CORS to the configured frontend origin

## Summary

Implement the cross-origin portion of final-SRS EI-CM-1 using FastAPI's standard
middleware. FRONTEND_ORIGIN permits one explicit frontend origin and defaults to
http://localhost:5173. Unsafe or malformed configuration fails at app creation.

## Changes

- Configure explicit origins, GET/POST methods, API request headers and exposed
  replay/request-ID response headers, without cookie credentials.
- Add allowed/disallowed-origin, preflight, configuration and safe-error tests.
- Document configuration, update traceability and record second-brain progress.

## Dependency and review

This PR is based on raghu/api-health (PR #9), not main. Retarget to main after
the health PR merges. Only C-owned C01 work is added. Request-ID generation,
body limits, global validation/errors and other members' endpoints remain later
increments. No merge or review approval is claimed.

## Validation

Passed locally: 44 pytest cases on Python 3.14, six SQLite readiness tests on
Python 3.11, changed-file syntax checks and git diff whitespace checks. Both
GitHub workflows passed at 7eaf19a, including Python 3.11 HTTP tests, lint,
package build and secret scanning. Final documentation changes trigger another
check. Review: https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API/pull/10.
Development record: brain/projects/2026-11-19-c01-cors.md.
