"""RLS contract for migration 0009 (training_phases)."""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot

_REPO = Path(__file__).resolve().parents[1]
_MIGRATION = _REPO / "schema" / "migrations" / "0009_training_phases.sql"

_DOMAIN_TABLES: frozenset[str] = frozenset({"training_phases"})


def test_0009_migration_exists() -> None:
    assert _MIGRATION.is_file()


def test_0009_sql_parses() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    statements = sqlglot.parse(sql, dialect="postgres")
    assert len(statements) > 0


def test_0009_enables_rls() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    enabled: set[str] = set()
    for m in re.finditer(
        r"ALTER\s+TABLE\s+(?:public\.)?(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
        sql,
        flags=re.IGNORECASE,
    ):
        enabled.add(m.group(1).lower())
    missing = sorted(_DOMAIN_TABLES - enabled)
    assert not missing, f"Tables missing RLS: {missing}"


def test_0009_auth_uid_policies() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    stripped = re.sub(r"--[^\n]*", "", sql)
    covered: set[str] = set()
    for m in re.finditer(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+(?:public\.)?(\w+)\s+.*?auth\.uid\(\)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        covered.add(m.group(1).lower())
    missing = sorted(_DOMAIN_TABLES - covered)
    assert not missing, f"Tables missing auth.uid policy: {missing}"
