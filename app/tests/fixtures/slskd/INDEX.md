# slskd offline fixtures (provenance)

| Fixture | Used by | Provenance |
|---------|---------|------------|
| `search_responses.json` | test_slskd_client (Phase 4) | [VERIFIED] 04-RESEARCH search responses shape |
| `transfer_completed.json` / `transfer_failed.json` / `transfer_stalled.json` | test_slskd_client / test_acquire (Phase 4) | [A3 — PINNED LIVE 2026-05-31] terminal-state strings (04-05-LIVE-PROBE.md) |
| `application.json` | test_shares (Phase 5, 05-02) | **[ASSUMED A3]** `GET /api/v0/application -> shares.files` is `[VERIFIED]` against the maintained gethomepage/homepage slskd widget (reads `appData.shares?.files`); the exact full body is **live-confirmed in plan 05-05** (one `curl …/application | jq .shares` on the NAS). The only field 05-02 reads is `shares.files` (int). |
