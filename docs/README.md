# Sentinel AI documentation

## Baseline (frozen)
- `../Sentinel AI Project Document.md` (**D1**): vision, goals, capabilities, scope.
- `../Sentinel AI Technical Design Document and Roadmap.md` (**D2**): build specification.

## Consolidated specification (v1.1-draft, amends the baseline)
These documents amend D1 and D2. Where they conflict, the amendment wins, and every conflict is logged in `00`. Anything not amended still applies as written.

| Doc | Contents |
|---|---|
| [design/00-consolidation-log.md](design/00-consolidation-log.md) | Owner decisions, review findings, contradictions resolved, the no-weakening rule |
| [design/01-architecture.md](design/01-architecture.md) | Invariants, architecture, processes, networks, tenancy, privacy, provider independence, delivery modes, flows |
| [design/02-data-model.md](design/02-data-model.md) | Schema amendments, RLS, audit chain fix, authority matrix |
| [design/03-workflow-and-predictions.md](design/03-workflow-and-predictions.md) | State machine amendments, budgets, signature changes, the prediction engine |
| [design/04-security-and-threat-model.md](design/04-security-and-threat-model.md) | Assets, adversaries, trust boundaries, compromise analysis, sandbox host rules, approvals, keys |
| [design/05-api.md](design/05-api.md) | API amendments and new events |
| [design/06-schemas.md](design/06-schemas.md) | Appendices A (model outputs), B (protocols, proof), C (config files) |
| [design/07-reliability-and-operations.md](design/07-reliability-and-operations.md) | Reliability requirements, failure modes, backup and DR, observability, deployment, runbooks |
| [design/08-external-dependencies.md](design/08-external-dependencies.md) | Dependencies, operational requirements, licences, free-tier feasibility |
| [design/09-scope.md](design/09-scope.md) | MVP vs V1 vs later |
| [design/10-roadmap.md](design/10-roadmap.md) | Build sequence and dependencies, critical path, risk register, open issues |
| [design/11-definition-of-done.md](design/11-definition-of-done.md) | Acceptance-test catalogue, component, MVP and V1 definitions of done |
| [design/12-traceability.md](design/12-traceability.md) | Every requirement → component → phase → acceptance tests |
| [design/13-phase-0-plan.md](design/13-phase-0-plan.md) | Phase 0 tasks and exit gate |
| [adr/](adr/README.md) | Architecture decision records |

## ID conventions
- `FR-nn` / `NFR-nn`: requirements (01–20 from D2; 21+ new).
- `INV-n`: system invariants.
- `AT-<AREA>-nn`: acceptance tests.
- `Bnn[a]`: build steps.
- `P0-nn`: Phase 0 tasks.
- `R-nn`: contradiction resolutions (00 §0.4); `RK-nn`: risks (10 §6).
- `OI-nn`: open issues.
- `OD-nn`: owner decisions.
