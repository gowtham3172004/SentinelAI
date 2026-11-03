"""Domain enumerations, mirrored exactly by PostgreSQL enum types (D2 §13.1, 02 §4).

``PG_ENUMS`` maps each database type name to its Python enum. A database test asserts that
the values and their order match the migrated schema, so the two can never drift.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

# Audit chain for platform-level actions (tenant chains use the tenant id).
PLATFORM_CHAIN_ID = UUID("00000000-0000-0000-0000-000000000000")


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    TRIAGING = "triaging"
    INVESTIGATING = "investigating"
    AWAITING_HUMAN = "awaiting_human"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    CLOSED = "closed"
    SUPPRESSED = "suppressed"


class SeverityLevel(StrEnum):
    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class IncidentClass(StrEnum):
    CODE_DEFECT = "code_defect"
    REGRESSION_FROM_RELEASE = "regression_from_release"
    CONFIGURATION = "configuration"
    DEPENDENCY_CHANGE = "dependency_change"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    EXTERNAL_DEPENDENCY = "external_dependency"
    INFRASTRUCTURE = "infrastructure"
    PERFORMANCE = "performance"
    DATA_ISSUE = "data_issue"
    UNKNOWN = "unknown"


class RemediationCategory(StrEnum):
    CODE_PATCH = "code_patch"
    CONFIG_CHANGE = "config_change"
    ROLLBACK = "rollback"
    DEPENDENCY_PIN = "dependency_pin"
    CAPACITY = "capacity"
    EXTERNAL_ESCALATION = "external_escalation"
    INFRA_ESCALATION = "infra_escalation"
    DATA_REPAIR = "data_repair"
    HUMAN_INVESTIGATION = "human_investigation"


class RunState(StrEnum):
    QUEUED = "queued"
    FREEZE_EVIDENCE = "freeze_evidence"
    TRIAGE = "triage"
    CORRELATE = "correlate"
    HYPOTHESIZE = "hypothesize"
    VALIDATE = "validate"
    REPRODUCE = "reproduce"
    PATCH = "patch"
    VERIFY = "verify"
    DIAGNOSE_FAILURE = "diagnose_failure"
    RISK_REVIEW = "risk_review"
    REPORT = "report"
    DONE = "done"


class RunOutcome(StrEnum):
    PROPOSAL_READY = "proposal_ready"
    REPORT_ONLY = "report_only"
    ESCALATED_INSUFFICIENT_EVIDENCE = "escalated_insufficient_evidence"
    ESCALATED_NOT_REPRODUCED = "escalated_not_reproduced"
    ESCALATED_VERIFY_FAILED = "escalated_verify_failed"
    ESCALATED_POLICY = "escalated_policy"
    ESCALATED_BUDGET = "escalated_budget"
    ESCALATED_DEPENDENCY_UNAVAILABLE = "escalated_dependency_unavailable"  # R-01
    CANCELLED = "cancelled"
    FAILED_INTERNAL = "failed_internal"


class EvidenceLevel(StrEnum):
    HYPOTHESIS = "hypothesis"
    SUPPORTED = "supported"
    REPRODUCED = "reproduced"
    VERIFIED = "verified"


class TrustLevel(StrEnum):
    SYSTEM = "system"
    UNTRUSTED_APP = "untrusted_app"
    UNTRUSTED_REPO = "untrusted_repo"
    ADVISORY = "advisory"
    HUMAN = "human"  # FR-33 run notes


class ReproOutcome(StrEnum):
    REPRODUCED = "reproduced"
    INCONCLUSIVE = "inconclusive"
    NOT_REPRODUCED = "not_reproduced"
    NOT_REPRODUCIBLE_HERE = "not_reproducible_here"


class PatchStatus(StrEnum):
    """Orchestrator-owned statuses only; approval and delivery state is derived (B4)."""

    PROPOSED = "proposed"
    PRECHECKED = "prechecked"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    VERIFIED_WITH_LIMITATIONS = "verified_with_limitations"
    FAILED = "failed"
    ADVISORY = "advisory"
    SUPERSEDED = "superseded"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    VERIFIED_WITH_LIMITATIONS = "verified_with_limitations"
    FAILED = "failed"
    UNVERIFIED_ADVISORY = "unverified_advisory"


class CheckResult(StrEnum):
    PASS = "pass"  # noqa: S105 - a gate result, not a password
    FAIL = "fail"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class UserRole(StrEnum):
    VIEWER = "viewer"
    RESPONDER = "responder"
    APPROVER = "approver"
    ADMIN = "admin"


class DeliveryState(StrEnum):
    QUEUED = "queued"
    TOKEN_VERIFIED = "token_verified"  # noqa: S105 - a delivery state, not a secret
    BRANCH_PUSHED = "branch_pushed"
    CI_RUNNING = "ci_running"
    CI_PASSED = "ci_passed"
    STAGING_DEPLOYING = "staging_deploying"
    STAGING_VERIFYING = "staging_verifying"
    AWAITING_PROMOTION = "awaiting_promotion"
    CANARY_5 = "canary_5"
    CANARY_25 = "canary_25"
    CANARY_100 = "canary_100"
    MONITORING = "monitoring"
    SUCCEEDED = "succeeded"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"
    FAILED_SAFE = "failed_safe"


class CaseOutcome(StrEnum):
    ACCEPTED = "accepted"
    ACCEPTED_WITH_CHANGES = "accepted_with_changes"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"
    ABSTAINED = "abstained"


PG_ENUMS: dict[str, type[StrEnum]] = {
    "incident_status": IncidentStatus,
    "severity_level": SeverityLevel,
    "incident_class": IncidentClass,
    "remediation_category": RemediationCategory,
    "run_state": RunState,
    "run_outcome": RunOutcome,
    "evidence_level": EvidenceLevel,
    "trust_level": TrustLevel,
    "repro_outcome": ReproOutcome,
    "patch_status": PatchStatus,
    "verification_status": VerificationStatus,
    "check_result": CheckResult,
    "risk_level": RiskLevel,
    "approval_decision": ApprovalDecision,
    "user_role": UserRole,
    "delivery_state": DeliveryState,
    "case_outcome": CaseOutcome,
}
