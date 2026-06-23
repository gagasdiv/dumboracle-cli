"""Interactive menu. The golden rule: a single operation failing must never
kill the tool -- it logs the error and drops back to the menu."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import clienttools, operations, ui
from .config import Config, ConfigError, Connection, load_config
from .db import Db, client_version_str, connect, init_thick_client


def _safe(fn, *args) -> None:
    """Run an operation, swallowing anything short of a hard exit."""
    try:
        fn(*args)
    except ui.Abort:
        ui.info("Cancelled.")
    except KeyboardInterrupt:
        ui.info("Cancelled.")
    except Exception as exc:  # noqa: BLE001
        ui.error(str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc))
        ui.info("Returned to menu.")


def _operations_menu(db: Db, bin_dir: Optional[str]) -> None:
    container = db.current_container()
    where = db.cfg.header_line()
    if container:
        where += f"  [container: {container}]"

    options = [
        ("create", "Create user + standard grants"),
        ("rename", "Rename user / schema"),
        ("supp", "Enable supplemental logging (schema tables)"),
        ("export", "Export schema (expdp)"),
        ("import", "Import schema (impdp, auto-detect)"),
        ("clone", "Clone schema -> <user>--yyyymmdd-hhii"),
    ]
    while True:
        choice = ui.choose(f"Connected: {where}", options, back_label="Disconnect / change DB")
        if choice is None:
            return
        if choice == "create":
            _safe(operations.create_user, db)
        elif choice == "rename":
            _safe(operations.rename_user, db)
        elif choice == "supp":
            _safe(operations.enable_supplemental_logging, db)
        elif choice == "export":
            _safe(operations.export_schema, db, bin_dir)
        elif choice == "import":
            _safe(operations.import_schema, db, bin_dir)
        elif choice == "clone":
            _safe(operations.clone_schema, db, bin_dir)


def _connect_and_run(conn_cfg: Connection, bin_dir: Optional[str]) -> None:
    ui.info(f"Connecting to {conn_cfg.name} ...")
    connection = connect(conn_cfg)
    db = Db(connection, conn_cfg)
    try:
        ui.ok(f"Connected. {db.version_banner()}")
        _operations_menu(db, bin_dir)
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
        ui.info(f"Disconnected from {conn_cfg.name}.")


def _render_connection_list(config: Config) -> None:
    ui.header("Choose a database")
    for idx, c in enumerate(config.connections, start=1):
        markers = ""
        if c.alias:
            markers += f"  [alias: {c.alias}]"
        if c.sysdba:
            markers += "  [SYSDBA]"
        print(f"  {idx:>2}. {c.name}  ({c.username}@{c.dsn}){markers}")
        if c.description:
            print(f"        {c.description}")
    print(f"  {0:>2}. Quit")
    print()
    ui.info("Pick by number or alias.")


def _choose_connection(config: Config) -> Optional[Connection]:
    """Render the connection list; accept a number OR a (case-insensitive) alias."""
    _render_connection_list(config)
    while True:
        try:
            raw = ui.read_line("  Select: ").strip()
        except ui.Abort:
            return None
        if raw in ("0", ""):
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(config.connections):
                return config.connections[n - 1]
            ui.warn("Invalid selection, try again.")
            continue
        match = next((c for c in config.connections if c.matches_alias(raw)), None)
        if match is not None:
            return match
        ui.warn("Invalid selection, try again.")


def _connection_menu(config: Config) -> None:
    while True:
        conn_cfg = _choose_connection(config)
        if conn_cfg is None:
            return
        _safe(_connect_and_run, conn_cfg, config.datapump_bin_dir)


def _run_menu(config_path: Optional[str]) -> int:
    ui.header("dumb-oracle")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        ui.error(str(exc))
        return 2

    try:
        init_thick_client(config.oracle_client_lib_dir)
        version = client_version_str()
        if version:
            ui.ok(f"Oracle thick client initialised (client {version}).")
        else:
            ui.ok("Oracle thick client initialised.")
    except Exception as exc:  # noqa: BLE001
        ui.error(f"Could not initialise Oracle thick client: {str(exc).splitlines()[0]}")
        ui.info(
            "Set 'oracle_client_lib_dir' in the config to your Oracle client/bin "
            "folder (or run 'extract-client' / 'wire-path')."
        )
        return 3

    try:
        _connection_menu(config)
    except (KeyboardInterrupt, ui.Abort):
        print()
    ui.info("Bye.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="dumb-oracle",
        description="A small, error-tolerant Oracle admin CLI for local databases.",
    )
    parser.add_argument(
        "-c", "--config", help="Path to connections.yaml", default=None
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("menu", help="Interactive admin menu (default).")

    p_extract = sub.add_parser(
        "extract-client",
        help="Unzip bundled Instant Client (basic+tools) and wire connections.yaml.",
    )
    p_extract.add_argument(
        "--dir", default=".", help="Folder holding the instantclient-*.zip files (default: .)"
    )

    sub.add_parser(
        "doctor",
        help="Diagnostic only: report Oracle tooling health (no changes).",
    )
    sub.add_parser(
        "wire-path",
        help="Point connections.yaml at PATH/system Oracle tools (only if healthy).",
    )

    args = parser.parse_args(argv)

    if args.command == "extract-client":
        return clienttools.extract_client(args.dir, args.config)
    if args.command == "doctor":
        return clienttools.doctor()
    if args.command == "wire-path":
        return clienttools.wire_path(args.config)

    # Default / "menu": run the interactive tool.
    return _run_menu(args.config)


if __name__ == "__main__":
    sys.exit(main())
