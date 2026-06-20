"""Contract tests: 0004_signal_layers migration RLS on new tables."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MIGRATION = _REPO / "schema" / "migrations" / "0004_signal_layers.sql"

_NEW_TABLES: frozenset[str] = frozenset({"metric_baselines", "metric_patterns"})


def test_0004_migration_exists() -> None:
    assert _MIGRATION.is_file()


def test_0004_enables_rls_on_signal_tables() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    enabled: set[str] = set()
    for m in re.finditer(
        r"ALTER\s+TABLE\s+(?:public\.)?(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY\s*;",
        sql,
        flags=re.IGNORECASE,
    ):
        enabled.add(m.group(1).lower())
    missing = sorted(_NEW_TABLES - enabled)
    assert not missing, f"Tables missing ENABLE ROW LEVEL SECURITY: {missing}"


def test_0004_defines_auth_uid_policies() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    stripped = re.sub(r"--[^\n]*", "", sql)
    covered: set[str] = set()
    for m in re.finditer(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+(?:public\.)?(\w+)\s+.*?auth\.uid\(\)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        covered.add(m.group(1).lower())
    missing = sorted(_NEW_TABLES - covered)
    assert not missing, f"Tables missing RLS policy with auth.uid(): {missing}"
