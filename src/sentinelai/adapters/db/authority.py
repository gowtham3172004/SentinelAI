"""Authority matrix loader and SQL grant generator (Phase 0 task P0-06).

The YAML file next to this module is the single source of truth for database write
authority. ``grants_sql`` renders the GRANT statements a migration embeds. The database test
AT-DB-02 checks the migrated database against the matrix, cell by cell.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Privilege = Literal["select", "insert", "update", "delete", "truncate"]
PRIVILEGES: tuple[Privilege, ...] = ("select", "insert", "update", "delete", "truncate")
Scope = Literal["platform", "tenant", "audit_chain"]

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_FUNCTION = re.compile(r"^[a-z_][a-z0-9_]{0,62}\((?:[a-z]+(?:, [a-z]+)*)?\)$")


class TableAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: Scope
    insert_only: bool = False
    sequence: str | None = None
    grants: dict[str, tuple[Privilege, ...]] = Field(default_factory=dict)


def _check_table(table: str, spec: TableAuthority, known: set[str]) -> None:
    if spec.sequence is not None and not _IDENT.match(spec.sequence):
        raise ValueError(f"{table}: invalid sequence name {spec.sequence!r}")
    for role, privileges in spec.grants.items():
        granted = set(privileges)
        if role not in known:
            raise ValueError(f"{table}: unknown role {role!r}")
        if len(granted) != len(privileges):
            raise ValueError(f"{table}/{role}: duplicate privileges")
        if "truncate" in granted:
            raise ValueError(f"{table}/{role}: TRUNCATE is never granted")
        if spec.insert_only and {"update", "delete"} & granted:
            raise ValueError(f"{table}/{role}: insert-only table cannot grant update/delete")
        if spec.scope == "audit_chain" and granted - {"select"}:
            raise ValueError(f"{table}/{role}: audit tables are written only via functions")


class AuthorityMatrix(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    roles: tuple[str, ...]
    tables: dict[str, TableAuthority]
    functions: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enforce_rules(self) -> Self:
        known = set(self.roles)
        for name in (*self.roles, *self.tables):
            if not _IDENT.match(name):
                raise ValueError(f"invalid identifier: {name!r}")
        for table, spec in self.tables.items():
            _check_table(table, spec, known)
        for signature, roles in self.functions.items():
            if not _FUNCTION.match(signature):
                raise ValueError(f"invalid function signature: {signature!r}")
            unknown = set(roles) - known
            if unknown:
                raise ValueError(f"{signature}: unknown roles {sorted(unknown)}")
        return self

    def allows(self, role: str, table: str, privilege: Privilege) -> bool:
        return privilege in self.tables[table].grants.get(role, ())

    def tables_with_scope(self, scope: Scope) -> list[str]:
        return [name for name, spec in self.tables.items() if spec.scope == scope]


def default_matrix_path() -> Path:
    return Path(str(files("sentinelai.adapters.db").joinpath("authority.yaml")))


def load_matrix(path: Path | None = None) -> AuthorityMatrix:
    source = path or default_matrix_path()
    return AuthorityMatrix.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))


def grants_sql(matrix: AuthorityMatrix, tables: list[str] | None = None) -> str:
    """Deterministic GRANT/REVOKE statements for ``tables`` (default: all) and all functions."""
    selected = tables if tables is not None else list(matrix.tables)
    lines: list[str] = []
    for table in selected:
        spec = matrix.tables[table]
        lines.append(f"REVOKE ALL ON TABLE {table} FROM PUBLIC;")
        for role in matrix.roles:
            privileges = spec.grants.get(role, ())
            if privileges:
                ordered = [p.upper() for p in PRIVILEGES if p in privileges]
                lines.append(f"GRANT {', '.join(ordered)} ON TABLE {table} TO {role};")
        if spec.sequence:
            writers = [r for r in matrix.roles if "insert" in spec.grants.get(r, ())]
            lines.append(f"REVOKE ALL ON SEQUENCE {spec.sequence} FROM PUBLIC;")
            if writers:
                lines.append(f"GRANT USAGE ON SEQUENCE {spec.sequence} TO {', '.join(writers)};")
    if tables is None:
        for signature, roles in matrix.functions.items():
            lines.append(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC;")
            if roles:
                lines.append(f"GRANT EXECUTE ON FUNCTION {signature} TO {', '.join(roles)};")
    return "\n".join(lines) + "\n"
