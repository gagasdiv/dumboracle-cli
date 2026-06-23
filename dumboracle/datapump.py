"""Run expdp / impdp and auto-detect what's inside a dump set.

Data Pump dump/log files live in a server-side DIRECTORY object. Because we
only target *local* databases, that directory is just a folder on this machine,
so the export folder the user asked for (``./<user>--yyyymmdd-hhii``) doubles as
the Data Pump directory.

Connection credentials are passed as a single argv element (``user/pass@dsn``)
and the rest of the parameters go in a ``parfile`` written into the working
directory, so we never fight the shell over quoting.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence

from . import ui
from .config import Connection


class DataPumpError(Exception):
    pass


def _resolve_exe(name: str, bin_dir: Optional[str]) -> str:
    if bin_dir:
        candidate = os.path.join(bin_dir, name)
        found = shutil.which(candidate) or (
            candidate if os.path.isfile(candidate) else None
        )
        # On Windows the extension may be needed.
        if not found and os.name == "nt":
            exe = candidate + ".exe"
            found = exe if os.path.isfile(exe) else None
        if found:
            return found
        raise DataPumpError(
            f"{name} not found in datapump_bin_dir: {bin_dir}"
        )
    found = shutil.which(name)
    if not found:
        raise DataPumpError(
            f"'{name}' not found on PATH. Set 'datapump_bin_dir' in the config "
            f"to your Oracle bin folder (a full client or DB install; "
            f"Instant Client does not include {name})."
        )
    return found


def _connect_arg(conn: Connection) -> str:
    cred = f"{conn.username}/{conn.password}@{conn.dsn}"
    if conn.sysdba:
        cred += ' as sysdba'
    return cred


def _write_parfile(work_dir: str, params: Sequence[str], name: str) -> str:
    path = os.path.join(work_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(params) + "\n")
    return path


def _run(exe: str, conn: Connection, parfile_name: str, work_dir: str) -> int:
    """Run a Data Pump exe streaming output live. Returns the exit code."""
    argv = [exe, _connect_arg(conn), f"parfile={parfile_name}"]
    ui.info(f"$ {os.path.basename(exe)} <credentials> parfile={parfile_name}")
    ui.rule()
    proc = subprocess.Popen(
        argv,
        cwd=work_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write("  | " + line)
    proc.wait()
    ui.rule()
    return proc.returncode


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def run_export(
    conn: Connection,
    bin_dir: Optional[str],
    work_dir: str,
    dir_object: str,
    schema: str,
    dumpfile: str,
    logfile: str,
) -> None:
    exe = _resolve_exe("expdp", bin_dir)
    params = [
        f"DIRECTORY={dir_object}",
        f"DUMPFILE={dumpfile}",
        f"LOGFILE={logfile}",
        f"SCHEMAS={schema}",
        "REUSE_DUMPFILES=YES",
    ]
    parfile = _write_parfile(work_dir, params, "export.par")
    code = _run(exe, conn, os.path.basename(parfile), work_dir)
    # expdp returns 5 for "completed with warnings"; treat 0 and 5 as acceptable.
    if code not in (0, 5):
        raise DataPumpError(f"expdp exited with code {code}")


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def find_dump_files(directory: str) -> List[str]:
    """Auto-find dump files: any ``*.dmp`` in the folder, sorted naturally."""
    files = [
        f for f in os.listdir(directory)
        if f.lower().endswith(".dmp") and os.path.isfile(os.path.join(directory, f))
    ]

    def key(name: str):
        return [
            int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)
        ]

    return sorted(files, key=key)


def detect_metadata(
    conn: Connection,
    bin_dir: Optional[str],
    work_dir: str,
    dir_object: str,
    dump_files: Sequence[str],
) -> dict:
    """Use ``impdp ... sqlfile=`` to extract DDL, then scrape it.

    Returns ``{"schemas": [...], "tablespaces": [...]}``. Nothing is imported;
    sqlfile mode only reads metadata and writes SQL to the directory.
    """
    exe = _resolve_exe("impdp", bin_dir)
    sqlfile = f"__detect_{os.getpid()}.sql"
    params = [
        f"DIRECTORY={dir_object}",
        f"DUMPFILE={','.join(dump_files)}",
        f"SQLFILE={sqlfile}",
        "NOLOGFILE=YES",
    ]
    parfile = _write_parfile(work_dir, params, "detect.par")
    code = _run(exe, conn, os.path.basename(parfile), work_dir)

    sql_path = os.path.join(work_dir, sqlfile)
    schemas: List[str] = []
    tablespaces: List[str] = []
    if os.path.isfile(sql_path):
        with open(sql_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        schemas = _scrape_schemas(text)
        tablespaces = _scrape_tablespaces(text)
        try:
            os.remove(sql_path)
        except OSError:
            pass
    if code not in (0, 5) and not schemas:
        raise DataPumpError(
            f"impdp metadata detection failed (exit {code}); "
            f"could not read the dump set."
        )
    return {"schemas": schemas, "tablespaces": tablespaces}


_SYSTEM_TABLESPACES = {"SYSTEM", "SYSAUX", "TEMP", "UNDOTBS1", "UNDOTBS2", "USERS"}


def _scrape_schemas(text: str) -> List[str]:
    found: List[str] = []

    def add(name: str) -> None:
        if name and name not in found:
            found.append(name)

    for m in re.finditer(r'CREATE USER\s+"([^"]+)"', text, re.IGNORECASE):
        add(m.group(1))
    if found:
        return found

    # Fall back to the most common schema qualifier "SCHEMA"."OBJECT".
    counts: dict = {}
    for m in re.finditer(r'"([A-Z0-9_$#]+)"\."', text):
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    for name in sorted(counts, key=lambda k: counts[k], reverse=True):
        add(name)
    return found


def _scrape_tablespaces(text: str) -> List[str]:
    found: List[str] = []
    for m in re.finditer(r'TABLESPACE\s+"([^"]+)"', text, re.IGNORECASE):
        name = m.group(1)
        if name not in found:
            found.append(name)
    return found


def run_import(
    conn: Connection,
    bin_dir: Optional[str],
    work_dir: str,
    dir_object: str,
    dump_files: Sequence[str],
    logfile: str,
    remap_schema: Optional[tuple] = None,
    remap_tablespaces: Optional[Sequence[tuple]] = None,
    network_link: Optional[str] = None,
    source_schema: Optional[str] = None,
) -> None:
    exe = _resolve_exe("impdp", bin_dir)
    params = [f"DIRECTORY={dir_object}", f"LOGFILE={logfile}"]
    if network_link:
        params.append(f"NETWORK_LINK={network_link}")
        if source_schema:
            params.append(f"SCHEMAS={source_schema}")
    else:
        params.append(f"DUMPFILE={','.join(dump_files)}")
    if remap_schema:
        src, tgt = remap_schema
        params.append(f'REMAP_SCHEMA={src}:"{tgt}"')
    for src_ts, tgt_ts in (remap_tablespaces or []):
        params.append(f'REMAP_TABLESPACE={src_ts}:"{tgt_ts}"')
    # Don't fail the whole import just because objects already exist.
    params.append("TABLE_EXISTS_ACTION=SKIP")

    parfile = _write_parfile(work_dir, params, "import.par")
    code = _run(exe, conn, os.path.basename(parfile), work_dir)
    if code not in (0, 5):
        raise DataPumpError(f"impdp exited with code {code}")
