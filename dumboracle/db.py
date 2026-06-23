"""Thin wrapper around python-oracledb (thick mode) with admin helpers.

All SQL helpers live here so the operations layer stays readable. Identifiers
are always quoted via :func:`util.q` so names with hyphens (the
``<user>--yyyymmdd-hhii`` convention) work everywhere.
"""

from __future__ import annotations

from typing import List, Optional

import oracledb

from . import ui
from .config import Connection
from .util import q, qlit

_THICK_INITED = False

# Reusable Oracle DIRECTORY object that we re-point at whatever folder the
# current export/import needs.
DIR_OBJECT = "DUMB_DP_DIR"


def init_thick_client(lib_dir: Optional[str]) -> None:
    """Initialise python-oracledb thick mode (idempotent)."""
    global _THICK_INITED
    if _THICK_INITED:
        return
    try:
        if lib_dir:
            oracledb.init_oracle_client(lib_dir=lib_dir)
        else:
            oracledb.init_oracle_client()
        _THICK_INITED = True
    except oracledb.Error as exc:
        # Already initialised in this process is fine.
        if "DPI-1047" in str(exc):
            raise
        _THICK_INITED = True


def client_version_str() -> Optional[str]:
    """Dotted thick-client version (e.g. '23.26.2.0.0'), or None if unavailable."""
    try:
        parts = oracledb.clientversion()
    except Exception:  # noqa: BLE001 - thin mode / not initialised
        return None
    if not parts:
        return None
    return ".".join(str(p) for p in parts)


def connect(conn: Connection) -> oracledb.Connection:
    kwargs = dict(user=conn.username, password=conn.password, dsn=conn.dsn)
    if conn.sysdba:
        kwargs["mode"] = oracledb.AUTH_MODE_SYSDBA
    return oracledb.connect(**kwargs)


class Db:
    """Convenience facade over an open connection."""

    def __init__(self, connection: oracledb.Connection, conn_cfg: Connection):
        self.conn = connection
        self.cfg = conn_cfg

    # -- low level --------------------------------------------------------
    def exec(self, sql: str, params: Optional[dict] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})

    def query_scalar(self, sql: str, params: Optional[dict] = None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            row = cur.fetchone()
            return row[0] if row else None

    def query_col(self, sql: str, params: Optional[dict] = None) -> List[str]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params or {})
            return [r[0] for r in cur.fetchall()]

    def commit(self) -> None:
        self.conn.commit()

    # -- info -------------------------------------------------------------
    def version_banner(self) -> str:
        try:
            return self.query_scalar(
                "select banner from v$version where rownum = 1"
            ) or "unknown"
        except oracledb.Error:
            return "unknown"

    def current_container(self) -> Optional[str]:
        try:
            return self.query_scalar("select sys_context('userenv','con_name') from dual")
        except oracledb.Error:
            return None

    # -- users / schemas --------------------------------------------------
    def user_exists(self, username: str) -> bool:
        n = self.query_scalar(
            "select count(*) from dba_users where username = :u",
            {"u": username},
        )
        return bool(n)

    def list_schemas(self, only_non_system: bool = True) -> List[str]:
        sql = "select username from dba_users"
        if only_non_system:
            sql += (
                " where oracle_maintained = 'N'"
                if self._has_oracle_maintained()
                else " where username not in ('SYS','SYSTEM','OUTLN','DBSNMP')"
            )
        sql += " order by username"
        return self.query_col(sql)

    def _has_oracle_maintained(self) -> bool:
        try:
            n = self.query_scalar(
                "select count(*) from all_tab_columns "
                "where table_name = 'DBA_USERS' and column_name = 'ORACLE_MAINTAINED'"
            )
            return bool(n)
        except oracledb.Error:
            return False

    def list_tables(self, owner: str) -> List[str]:
        return self.query_col(
            "select table_name from dba_tables where owner = :o order by table_name",
            {"o": owner},
        )

    def kill_sessions(self, username: str) -> int:
        """Kill every session owned by ``username``. Returns how many were killed."""
        rows = self.query_col(
            "select sid || ',' || serial# || ',@' || inst_id "
            "from gv$session where username = :u",
            {"u": username},
        )
        killed = 0
        for sid_serial in rows:
            try:
                self.exec(f"alter system kill session {qlit(sid_serial)} immediate")
                killed += 1
            except oracledb.Error:
                pass
        return killed

    def create_user(self, username: str, password: str, tablespace: str = "USERS") -> None:
        self.exec(
            f"create user {q(username)} identified by {q(password)} "
            f"default tablespace {q(tablespace)} quota unlimited on {q(tablespace)}"
        )

    def set_password(self, username: str, password: str) -> None:
        self.exec(f'alter user {q(username)} identified by {q(password)} account unlock')

    def apply_standard_grants(self, username: str, tablespace: str = "USERS") -> None:
        """Grant the everyday set a working schema needs (mirrors the old scripts)."""
        statements = [
            f"grant connect, resource to {q(username)}",
            f"alter user {q(username)} quota unlimited on {q(tablespace)}",
            f"grant create session to {q(username)}",
            f"grant create table to {q(username)}",
            f"grant create view to {q(username)}",
            f"grant create sequence to {q(username)}",
            f"grant create procedure to {q(username)}",
            f"grant create trigger to {q(username)}",
            f"grant create synonym to {q(username)}",
            f"grant create database link to {q(username)}",
            f"grant create materialized view to {q(username)}",
            f"grant exp_full_database to {q(username)}",
            f"grant imp_full_database to {q(username)}",
        ]
        for stmt in statements:
            try:
                self.exec(stmt)
            except oracledb.Error as exc:
                ui.warn(f"grant skipped: {stmt.split(' to ')[0]} ({_short(exc)})")

    def drop_user_cascade(self, username: str) -> None:
        killed = self.kill_sessions(username)
        if killed:
            ui.info(f"killed {killed} active session(s) for {username}")
        self.exec(f"drop user {q(username)} cascade")

    def rename_user(self, old: str, new: str) -> None:
        """Rename a schema in-place via the SYS.USER$ dictionary trick.

        This is the only lightweight way to rename a schema; Oracle has no
        supported ``ALTER USER ... RENAME``. It needs SYS / SYSDBA. Sessions of
        the old user are killed first and the shared pool is flushed afterwards.
        """
        self.kill_sessions(old)
        self.exec(
            f"update sys.user$ set name = {qlit(new)} "
            f"where name = {qlit(old)} and type# = 1"
        )
        self.commit()
        for stmt in (
            "alter system flush shared_pool",
            "alter system checkpoint",
        ):
            try:
                self.exec(stmt)
            except oracledb.Error:
                pass

    # -- directory object -------------------------------------------------
    def ensure_directory(self, path: str) -> str:
        """Point the reusable DIRECTORY object at ``path`` and return its name."""
        self.exec(f"create or replace directory {DIR_OBJECT} as {qlit(path)}")
        try:
            self.exec(f"grant read, write on directory {DIR_OBJECT} to {q(self.cfg.username)}")
        except oracledb.Error:
            pass
        return DIR_OBJECT

    # -- supplemental logging --------------------------------------------
    def enable_db_min_supplemental_logging(self) -> None:
        self.exec("alter database add supplemental log data")

    def add_table_supplemental_log(self, owner: str, table: str) -> None:
        self.exec(
            f"alter table {q(owner)}.{q(table)} "
            f"add supplemental log data (all) columns"
        )


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0]
    return text[:120]
