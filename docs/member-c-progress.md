# Member C implementation sequence

Ownership follows the revised work-distribution PDF. Requirement details come
from the final 14-page SRS; older details are identified explicitly. These are
work sessions and review units, not fabricated calendar-day progress.

| Package | Next deliverable | Dependency / status |
| --- | --- | --- |
| C01 | API framework, health, CORS, errors, IDs and body limit | Health PR #9, CORS PR #10 and request-safety PR #11 await review. Shared-provider integration awaits A/H03. |
| C02 | Full OpenAPI 3.x contract and Prism mock | Can proceed; covers contracts for all owners without implementing their endpoints. |
| C03 | Transaction/posting/idempotency contracts, Clock/IdProvider and fakes | v0.1 implemented with 15 passing contract cases; issue #12 and PR #13 track review and A/B/D agreement. Not formally frozen. |
| C04 | Balanced posting for deposit, transfer and reversal | Consume A's actual migration 001 and transaction provider; never commit inside posting. |
| C05 | Failure-injection, rollback and crash harness | Depends on C04 and shared transaction integration. |
| C06 | Owner-checked transfer reads and paginated history | Depends on A's auth/provider, stable schema and posting data. |
| C07 | Concurrency and performance harness | Service cases after C04; full HTTP reference run after B's transfer endpoint. |
| C08 | History/concurrency frontend pages | Depends on A's React router/client shell; mock against C02 first. |
| C09 | Traceability and posting ADR | Update with every PR; posting ADR after C03/C04 decisions. |
| C10 | Five research notes through brain-ingest | Requires A/H06 workflow; development session notes do not count as these research notes. |

Each feature gets its own branch and reviewed PR, focused tests, a comparison
with current main, a CI result and a linked brain note. Dependent PRs stay based
on their predecessor until it merges; then retarget/rebase and rerun CI. Only
merge after the required human review. No handover gate is marked complete
while its schema/provider or integration checks are missing.
