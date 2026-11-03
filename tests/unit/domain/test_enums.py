from __future__ import annotations

from sentinelai.domain.enums import PG_ENUMS, PatchStatus, RunOutcome, TrustLevel


def test_every_enum_value_is_a_lowercase_identifier() -> None:
    for type_name, enum in PG_ENUMS.items():
        assert type_name.islower()
        values = [member.value for member in enum]
        assert len(values) == len(set(values)), type_name
        for value in values:
            assert value == value.lower()
            assert value.replace("_", "").isalnum()


def test_consolidation_amendments_are_present() -> None:
    assert RunOutcome.ESCALATED_DEPENDENCY_UNAVAILABLE in RunOutcome  # R-01
    assert TrustLevel.HUMAN in TrustLevel  # FR-33
    # B4: approval and delivery states are derived, never stored as patch status
    assert {"approved", "rejected", "delivered"}.isdisjoint({s.value for s in PatchStatus})
