"""Run psycopg2 work scoped to one Supabase user so RLS enforces isolation.

Soma connects to Postgres through a privileged pooler role (``postgres``) that
owns the tables and therefore *bypasses* row-level security. End-user surfaces
(the dashboard, coaching chat, text-to-SQL) must not rely on hand-written
``user_id`` filters — the project rule is that RLS enforces isolation. This
module switches the current transaction to the non-owner ``authenticated`` role
and binds ``auth.uid()`` to the requesting user via ``request.jwt.claims`` (the
same mechanism PostgREST uses after verifying a JWT), optionally marking the
transaction read-only.

Usage (one short-lived connection per unit of work — ``SET LOCAL`` reverts on
commit/rollback)::

    with _pg_conn() as conn:
        apply_rls_scope(conn, user_id=user_id, read_only=True)
        # every subsequent query in this transaction runs as `authenticated`
        # with auth.uid() == user_id, so RLS restricts rows automatically.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_STATEMENT_TIMEOUT_MS = 15000


def jwt_claims_json(user_id: str) -> str:
    """Serialize the minimal Supabase JWT claims that drive ``auth.uid()``."""
    return json.dumps({"sub": user_id, "role": "authenticated"}, separators=(",", ":"))


def apply_rls_scope(
    conn: Any,
    *,
    user_id: str,
    read_only: bool = True,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> None:
    """Bind the current transaction to ``user_id`` under the ``authenticated`` role.

    Must run before any other statement in the transaction: ``SET TRANSACTION
    READ ONLY`` is only valid as the first command. After this call, RLS policies
    (``USING (user_id = auth.uid())``) restrict every read, and ``WITH CHECK``
    restricts every write, so cross-tenant access is impossible even if a query
    (e.g. LLM-generated SQL) omits or defeats an explicit ``user_id`` predicate.

    Args:
        conn: An open psycopg2 connection with no prior statements in the
            current transaction.
        user_id: The Supabase auth user id to scope to (bound as ``auth.uid()``).
        read_only: When True, marks the transaction ``READ ONLY`` so no write or
            side-effecting statement can run regardless of validation.
        statement_timeout_ms: Per-statement timeout guard.

    Raises:
        ValueError: If ``user_id`` is empty.
    """
    if not user_id:
        raise ValueError("apply_rls_scope requires a non-empty user_id")
    timeout = int(statement_timeout_ms)
    with conn.cursor() as cur:
        if read_only:
            cur.execute("SET TRANSACTION READ ONLY")
        # set_config(..., is_local=true) scopes the claim to this transaction only.
        cur.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (jwt_claims_json(user_id),),
        )
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute(f"SET LOCAL statement_timeout = {timeout}")
