# Phase 0 Handoff Checklist

Use this checklist before Phase 1 execution. If Phase 0 is accepted by operator override, record the override and keep the missing evidence visible.

| Area | Required Phase 1 handoff check | Status |
| --- | --- | --- |
| Phase 0 status | `docs/phase-0/phase0-status.md` reflects current reality or has an explicit override note. | Pending |
| AOI | Bangalore 60 km AOI, bbox, demo window, clear-season window, and sample fields are authoritative. | Complete |
| Provider credentials | Credential status captured without storing secrets in the repo. | Pending |
| VM | `akasha-staging` SSH, Docker, Compose, `/srv/akasha`, and egress are confirmed. | Pending |
| Storage | `/srv/akasha` capacity accepted as Phase 1-only or expanded before real backfills. | Pending |
| Scratch | Dedicated `/scratch/akasha` exists or `/srv/akasha/scratch` is accepted for Phase 1 fixtures. | Pending |
| Ports | Public listeners on 80, 443, 8080, and 8888 are audited before Caddy is deployed. | Pending |
| Azure NSG | NSG/firewall rules are exported and reviewed. | Pending |
| Secrets | SOPS age key custody and runtime decrypt flow are documented. | Pending |
| Backup target | PostgreSQL and config backup target is selected. | Pending |
| CI registry | CI platform and image registry are selected. | Pending |
| pgSTAC | pgSTAC adoption remains approved or a blocker is documented. | Pending |
