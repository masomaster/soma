"""Contract tests: initial migration enables RLS on every domain table (Phase 2)."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MIGRATION = _REPO / "schema" / "migrations" / "0001_initial.sql"

# Tables that must have RLS matching application multi-tenant model.
_DOMAIN_TABLES: frozenset[str] = frozenset(
    {
        "user_settings",
        "strength_events",
        "cardio_events",
        "biometrics",
        "daily_health_metrics",
        "daily_features",
        "interventions",
        "daily_briefings",
        "anomaly_events",
    }
)


def test_0001_initial_migration_exists() -> None:
    assert _MIGRATION.is_file(), f"Expected {_MIGRATION}"


def test_0001_enables_rls_on_all_domain_tables() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    enabled: set[str] = set()
    for m in re.finditer(
        r"ALTER\s+TABLE\s+(?:public\.)?(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY\s*;",
        sql,
        flags=re.IGNORECASE,
    ):
        enabled.add(m.group(1).lower())
    missing = sorted(_DOMAIN_TABLES - enabled)
    assert not missing, f"Tables missing ENABLE ROW LEVEL SECURITY: {missing}"


def test_0001_defines_auth_uid_policies_on_domain_tables() -> None:
    """Each domain table should have a policy using auth.uid() for tenant isolation."""
    sql = _MIGRATION.read_text(encoding="utf-8")
    # Strip SQL comments so commented-out policies do not satisfy the contract.
    stripped = re.sub(r"--[^\n]*", "", sql)
    covered: set[str] = set()
    for m in re.finditer(
        r"CREATE\s+POLICY\s+\w+\s+ON\s+(?:public\.)?(\w+)\s+.*?auth\.uid\(\)",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        covered.add(m.group(1).lower())
    missing = sorted(_DOMAIN_TABLES - covered)
    assert not missing, f"Tables missing RLS policy with auth.uid(): {missing}"


def test_strength_events_includes_superset_id_column() -> None:
    """Phase 1 Hevy payload includes nullable superset_id (integrations-checklist)."""
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert re.search(
        r"\bsuperset_id\b",
        sql,
        flags=re.IGNORECASE,
    ), "strength_events should document superset_id for Hevy supersets"
