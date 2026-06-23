"""High-level operations wired to the menu.

Each function reads what it needs interactively and performs one task. They
raise on hard failure; the CLI loop catches everything and returns to the menu,
so a mistake never crashes the tool.
"""

from __future__ import annotations

import os
from typing import List

from . import datapump, ui
from .db import Db
from .util import norm_ident, q, stamped_name, timestamp

LOOPBACK_LINK = "DUMB_LOOPBACK"


# ---------------------------------------------------------------------------
# 1. Create user + standard grants
# ---------------------------------------------------------------------------
def create_user(db: Db) -> None:
    ui.header("Create user + grants")
    username = norm_ident(ui.ask("Username"))
    password = ui.ask_secret("Password", default=username)
    if not password:
        password = username
    tablespace = norm_ident(ui.ask("Default tablespace", default="USERS"))

    if db.user_exists(username):
        ui.warn(f"User {username} already exists.")
        if not ui.confirm(f"DROP USER {username} CASCADE and recreate?", default=False):
            ui.info("Left existing user untouched.")
            return
        db.drop_user_cascade(username)
        ui.ok(f"Dropped {username}.")

    db.create_user(username, password, tablespace)
    ui.ok(f"Created user {username} (default tablespace {tablespace}).")
    db.apply_standard_grants(username, tablespace)
    ui.ok("Standard grants applied.")


# ---------------------------------------------------------------------------
# 2. Rename user / schema
# ---------------------------------------------------------------------------
def rename_user(db: Db) -> None:
    ui.header("Rename user / schema")
    old = _pick_schema(db, "Schema to rename")
    if old is None:
        return
    new = norm_ident(ui.ask("New name", default=stamped_name(old)))

    if db.user_exists(new):
        ui.warn(f"Target name {new} already exists; choose another.")
        return
    if not ui.confirm(f"Rename {old} -> {new}? (needs SYS/SYSDBA)", default=False):
        return

    db.rename_user(old, new)
    ui.ok(f"Renamed {old} -> {new}.")
    ui.info("If the schema owns jobs/synonyms referencing the old name, review them.")


# ---------------------------------------------------------------------------
# 3. Supplemental logging
# ---------------------------------------------------------------------------
def enable_supplemental_logging(db: Db) -> None:
    ui.header("Enable supplemental logging")
    schema = _pick_schema(db, "Schema (all its tables)")
    if schema is None:
        return

    try:
        db.enable_db_min_supplemental_logging()
        ui.ok("Database minimal supplemental logging enabled.")
    except Exception as exc:  # noqa: BLE001
        ui.warn(f"Could not set DB-level supplemental logging (needs SYSDBA): {_one(exc)}")

    tables = db.list_tables(schema)
    if not tables:
        ui.warn(f"No tables found in {schema}.")
        return

    done, skipped = 0, 0
    for table in tables:
        try:
            db.add_table_supplemental_log(schema, table)
            done += 1
        except Exception as exc:  # noqa: BLE001
            # ORA-32588: supplemental logging attribute already exists.
            if "32588" in str(exc):
                skipped += 1
            else:
                ui.warn(f"{schema}.{table}: {_one(exc)}")
    ui.ok(f"Supplemental log (ALL) COLUMNS on {done} table(s); {skipped} already had it.")


# ---------------------------------------------------------------------------
# 4. Export (expdp)
# ---------------------------------------------------------------------------
def export_schema(db: Db, bin_dir) -> None:
    ui.header("Export schema (expdp)")
    schema = _pick_schema(db, "Schema to export")
    if schema is None:
        return

    folder = f"{schema}--{timestamp()}"
    work_dir = os.path.abspath(folder)
    os.makedirs(work_dir, exist_ok=True)
    ui.info(f"Output folder: {work_dir}")

    dir_object = db.ensure_directory(work_dir)
    dumpfile = f"{schema}_%U.dmp"
    logfile = f"{schema}.log"

    datapump.run_export(
        conn=db.cfg,
        bin_dir=bin_dir,
        work_dir=work_dir,
        dir_object=dir_object,
        schema=schema,
        dumpfile=dumpfile,
        logfile=logfile,
    )
    ui.ok(f"Export complete -> {work_dir}")


# ---------------------------------------------------------------------------
# 5. Import (impdp) with auto-detect
# ---------------------------------------------------------------------------
def import_schema(db: Db, bin_dir) -> None:
    ui.header("Import schema (impdp)")
    src_dir = ui.ask("Directory containing the dump").strip().strip('"')
    work_dir = os.path.abspath(src_dir)
    if not os.path.isdir(work_dir):
        ui.error(f"Not a directory: {work_dir}")
        return

    dump_files = datapump.find_dump_files(work_dir)
    if not dump_files:
        ui.error("No .dmp files found in that directory.")
        return
    ui.info(f"Found dump file(s): {', '.join(dump_files)}")

    dir_object = db.ensure_directory(work_dir)

    ui.info("Inspecting dump set...")
    meta = datapump.detect_metadata(db.cfg, bin_dir, work_dir, dir_object, dump_files)
    detected_schemas: List[str] = meta["schemas"]
    detected_tbs: List[str] = meta["tablespaces"]
    if detected_schemas:
        ui.info(f"Detected source schema(s): {', '.join(detected_schemas)}")
    else:
        ui.warn("Could not auto-detect source schema; you'll need to type it.")
    if detected_tbs:
        ui.info(f"Detected source tablespace(s): {', '.join(detected_tbs)}")

    default_source = detected_schemas[0] if detected_schemas else ""
    source_schema = norm_ident(ui.ask("Source schema in dump", default=default_source or None))
    target_schema = norm_ident(ui.ask("Target schema name", default=source_schema))

    target_tbs = norm_ident(ui.ask("Target tablespace for all objects", default="USERS"))

    # If the target already exists, offer to rename it out of the way first.
    if db.user_exists(target_schema):
        ui.warn(f"Target schema {target_schema} already exists.")
        rename_to = stamped_name(target_schema)
        if ui.confirm(f"Rename existing {target_schema} -> {rename_to} first?", default=True):
            db.rename_user(target_schema, rename_to)
            ui.ok(f"Renamed existing {target_schema} -> {rename_to}.")
        else:
            ui.info("Existing schema kept; objects that clash will be skipped.")

    # Pre-create the target so it lands in the requested tablespace and is usable.
    if not db.user_exists(target_schema):
        password = target_schema
        db.create_user(target_schema, password, target_tbs)
        db.apply_standard_grants(target_schema, target_tbs)
        ui.ok(f"Created target schema {target_schema} (password = name, tablespace {target_tbs}).")

    remap_schema = None if source_schema == target_schema else (source_schema, target_schema)
    remap_tbs = [(ts, target_tbs) for ts in detected_tbs if ts.upper() != target_tbs]
    logfile = f"import_{timestamp()}.log"

    datapump.run_import(
        conn=db.cfg,
        bin_dir=bin_dir,
        work_dir=work_dir,
        dir_object=dir_object,
        dump_files=dump_files,
        logfile=logfile,
        remap_schema=remap_schema,
        remap_tablespaces=remap_tbs,
    )
    ui.ok(f"Import complete into {target_schema}. Log: {os.path.join(work_dir, logfile)}")


# ---------------------------------------------------------------------------
# 6. Clone schema (network_link, no dump files)
# ---------------------------------------------------------------------------
def clone_schema(db: Db, bin_dir) -> None:
    ui.header("Clone schema")
    source = _pick_schema(db, "Schema to clone")
    if source is None:
        return
    target = norm_ident(ui.ask("Clone name", default=stamped_name(source)))

    if db.user_exists(target):
        ui.warn(f"Clone target {target} already exists; choose another name.")
        return

    work_dir = os.path.abspath(".")
    cfg = db.cfg

    # Loopback DB link back to this same database, so impdp can copy directly.
    ui.info(f"Creating loopback database link {LOOPBACK_LINK}...")
    try:
        db.exec(f"drop database link {LOOPBACK_LINK}")
    except Exception:  # noqa: BLE001
        pass
    db.exec(
        f"create database link {LOOPBACK_LINK} "
        f"connect to {q(cfg.username)} identified by {q(cfg.password)} "
        f"using '{cfg.dsn}'"
    )

    try:
        logfile = f"clone_{timestamp()}.log"
        # network_link import needs a directory object for its log file only.
        dir_object = db.ensure_directory(work_dir)
        datapump.run_import(
            conn=cfg,
            bin_dir=bin_dir,
            work_dir=work_dir,
            dir_object=dir_object,
            dump_files=[],
            logfile=logfile,
            remap_schema=(source, target),
            network_link=LOOPBACK_LINK,
            source_schema=source,
        )
        # Password isn't carried across usably, so set it to the clone's name.
        db.set_password(target, target)
        db.apply_standard_grants(target)
        ui.ok(f"Cloned {source} -> {target} (password = {target}).")
        ui.info(f"Clone log: {os.path.join(work_dir, logfile)}")
    finally:
        try:
            db.exec(f"drop database link {LOOPBACK_LINK}")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _pick_schema(db: Db, prompt: str):
    """Let the user pick an existing schema from a list, or type one."""
    schemas = db.list_schemas()
    if schemas:
        picked = ui.choose_from(prompt, schemas + ["<type a name>"], back_label="Cancel")
        if picked is None:
            return None
        if picked != "<type a name>":
            return norm_ident(picked)
    typed = ui.ask(prompt)
    name = norm_ident(typed)
    if not db.user_exists(name):
        ui.warn(f"Schema {name} does not exist.")
    return name


def _one(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:120]
