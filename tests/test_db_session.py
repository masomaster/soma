"""Tests for RLS-scoped transaction setup (pipeline.db_session)."""

from __future__ import annotations

import json

import pytest

from pipeline.db_session import apply_rls_scope, jwt_claims_json

UID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class _FakeCursor:
    def __init__(self, log: list[tuple[str, tuple]]):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._log.append((sql, params or ()))


class _FakeConn:
    def __init__(self):
        self.log: list[tuple[str, tuple]] = []

    def cursor(self):
        return _FakeCursor(self.log)


def test_jwt_claims_json_has_sub_and_role():
    payload = json.loads(jwt_claims_json(UID))
    assert payload["sub"] == UID
    assert payload["role"] == "authenticated"


def test_read_only_sets_transaction_read_only_first():
    conn = _FakeConn()
    apply_rls_scope(conn, user_id=UID, read_only=True)
    statements = [sql for sql, _ in conn.log]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert "SET LOCAL ROLE authenticated" in statements
    assert any("request.jwt.claims" in s for s in statements)
    assert any(s.startswith("SET LOCAL statement_timeout") for s in statements)


def test_read_only_binds_requesting_user():
    conn = _FakeConn()
    apply_rls_scope(conn, user_id=UID, read_only=True)
    claim_params = [p for s, p in conn.log if "request.jwt.claims" in s]
    assert claim_params, "jwt claims statement missing"
    assert json.loads(claim_params[0][0])["sub"] == UID


def test_write_mode_omits_read_only():
    conn = _FakeConn()
    apply_rls_scope(conn, user_id=UID, read_only=False)
    statements = [sql for sql, _ in conn.log]
    assert "SET TRANSACTION READ ONLY" not in statements
    assert "SET LOCAL ROLE authenticated" in statements


def test_empty_user_id_rejected():
    with pytest.raises(ValueError, match="non-empty user_id"):
        apply_rls_scope(_FakeConn(), user_id="", read_only=True)
