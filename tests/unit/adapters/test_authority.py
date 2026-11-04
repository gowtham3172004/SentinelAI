from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from sentinelai.adapters.db.authority import (
    AuthorityMatrix,
    default_matrix_path,
    grants_sql,
    load_matrix,
)


def base() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(default_matrix_path().read_text(encoding="utf-8"))
    return raw


def test_packaged_matrix_is_valid() -> None:
    matrix = load_matrix()
    assert "r_maint" in matrix.roles
    assert matrix.tables["audit_log"].scope == "audit_chain"
    assert set(matrix.tables_with_scope("tenant")) == {
        "services",
        "repositories",
        "repo_revisions",
        "release_events",
    }


def test_allows_lookup() -> None:
    matrix = load_matrix()
    assert matrix.allows("r_codeintel", "repo_revisions", "insert")
    assert not matrix.allows("r_orch", "repo_revisions", "insert")
    assert not matrix.allows("r_orch", "audit_log", "select")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda m: m["tables"]["users"]["grants"].update({"r_ghost": ["select"]}), "unknown role"),
        (lambda m: m["tables"]["jobs"]["grants"].update({"r_api": ["truncate"]}), "TRUNCATE"),
        (
            lambda m: m["tables"]["repo_revisions"]["grants"].update({"r_api": ["update"]}),
            "insert-only",
        ),
        (
            lambda m: m["tables"]["audit_log"]["grants"].update({"r_api": ["select", "insert"]}),
            "only via functions",
        ),
        (lambda m: m["tables"]["users"]["grants"].update({"r_api": ["select", "select"]}), "dup"),
        (lambda m: m["tables"].update({"Bad-Name": {"scope": "platform"}}), "identifier"),
        (lambda m: m["functions"].update({"f(); DROP TABLE x": ["r_api"]}), "signature"),
        (lambda m: m["functions"].update({"f()": ["r_ghost"]}), "unknown roles"),
        (lambda m: m["tables"]["users"].update({"scope": "global"}), "scope"),
    ],
)
def test_matrix_rules_are_enforced(mutate: Any, message: str) -> None:
    raw = base()
    mutate(raw)
    with pytest.raises(ValidationError, match=message):
        AuthorityMatrix.model_validate(raw)


def test_grants_sql_is_deterministic_and_complete() -> None:
    matrix = load_matrix()
    sql = grants_sql(matrix)
    assert sql == grants_sql(load_matrix())
    assert "REVOKE ALL ON TABLE tenants FROM PUBLIC;" in sql
    assert "GRANT SELECT, INSERT ON TABLE repo_revisions TO r_codeintel;" in sql
    assert "GRANT SELECT ON TABLE audit_log TO r_readonly;" in sql
    assert "GRANT USAGE ON SEQUENCE jobs_id_seq TO r_telemetry, r_api," in sql
    assert "TRUNCATE" not in sql
    platform_fn = "audit_append_platform(text, text, text, text, text, jsonb)"
    assert f"GRANT EXECUTE ON FUNCTION {platform_fn} TO r_api, r_maint;" in sql
    # every non-empty grant cell appears exactly once
    cells = sum(1 for spec in matrix.tables.values() for privs in spec.grants.values() if privs)
    assert sql.count(" ON TABLE ") - sql.count("REVOKE ALL ON TABLE") == cells


def test_grants_sql_for_selected_tables_omits_functions() -> None:
    sql = grants_sql(load_matrix(), tables=["users"])
    assert "users" in sql
    assert "tenants" not in sql
    assert "FUNCTION" not in sql


def test_load_matrix_from_explicit_path(tmp_path: Path) -> None:
    raw = base()
    raw["tables"] = {"tenants": raw["tables"]["tenants"]}
    path = tmp_path / "authority.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert list(load_matrix(path).tables) == ["tenants"]
