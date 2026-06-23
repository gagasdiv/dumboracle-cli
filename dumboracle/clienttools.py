"""Setup helpers: extract a bundled Instant Client, or wire up a system install.

Two entry points used by the CLI:

* :func:`extract_client` -- find the ``instantclient-basic`` (+ ``-tools``) zips
  sitting in a folder, sanity-check they match this OS, unzip them, and point
  ``connections.yaml`` at the result.
* :func:`doctor` -- when Oracle is already installed on the machine, locate
  ``sqlplus`` / ``expdp`` / ``impdp`` / the OCI library, report what's found, and
  offer to wire ``connections.yaml`` + print the PATH lines to add.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import socket
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import ui
from .config import set_top_level_keys


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------
def os_token() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def arch_token() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def oci_lib_names() -> List[str]:
    """Filenames that prove a usable OCI client lib lives in a folder."""
    if sys.platform.startswith("win"):
        return ["oci.dll"]
    if sys.platform == "darwin":
        return ["libclntsh.dylib"]
    return ["libclntsh.so"]


def _has_oci_lib(directory: str) -> bool:
    if not os.path.isdir(directory):
        return False
    for name in oci_lib_names():
        if os.path.isfile(os.path.join(directory, name)):
            return True
        # Linux often ships versioned files: libclntsh.so.23.1
        if glob.glob(os.path.join(directory, name + "*")):
            return True
    return False


def _dp_exe(name: str) -> str:
    return name + ".exe" if sys.platform.startswith("win") else name


# ---------------------------------------------------------------------------
# extract-client
# ---------------------------------------------------------------------------
def _find_zip(directory: str, *prefixes: str) -> Optional[str]:
    for fname in sorted(os.listdir(directory)):
        low = fname.lower()
        if low.endswith(".zip") and low.startswith(prefixes):
            return os.path.join(directory, fname)
    return None


def _zip_top_dir(zip_path: str) -> Optional[str]:
    with zipfile.ZipFile(zip_path) as zf:
        for entry in zf.namelist():
            parts = entry.replace("\\", "/").split("/")
            if parts and parts[0].startswith("instantclient"):
                return parts[0]
    return None


def _check_zip_platform(zip_path: str) -> Tuple[bool, str]:
    """Return (ok, message). Verifies the zip is for this OS (and warns on arch)."""
    name = os.path.basename(zip_path).lower()
    want_os = os_token()
    if want_os not in name:
        return False, (
            f"{os.path.basename(zip_path)} is not a '{want_os}' package "
            f"(this machine is {want_os}/{arch_token()})."
        )
    want_arch = arch_token()
    # Instant Client uses 'x64'; arm packages say 'arm64'/'aarch64'.
    if want_arch == "x64" and ("arm64" in name or "aarch64" in name):
        return False, f"{os.path.basename(zip_path)} looks like an ARM build, but this is x64."
    if want_arch == "arm64" and "x64" in name and "arm64" not in name and "aarch64" not in name:
        return False, f"{os.path.basename(zip_path)} looks like an x64 build, but this is arm64."
    return True, "ok"


def extract_client(search_dir: str, config_path: Optional[str]) -> int:
    ui.header("Extract bundled Instant Client")
    search_dir = os.path.abspath(search_dir)
    ui.info(f"Looking for Instant Client zips in: {search_dir}")
    ui.info(f"This machine: {os_token()}/{arch_token()}")

    basic = _find_zip(search_dir, "instantclient-basic")
    tools = _find_zip(search_dir, "instantclient-tools")

    if not basic:
        ui.error(
            "No 'instantclient-basic*.zip' (or basiclite) found. "
            "Download it from Oracle and drop it in this folder."
        )
        return 2
    ui.ok(f"Found basic package: {os.path.basename(basic)}")
    if tools:
        ui.ok(f"Found tools package: {os.path.basename(tools)}")
    else:
        ui.warn(
            "No 'instantclient-tools*.zip' found. Thick mode will work, but "
            "export/import/clone (expdp/impdp) will not until you add the tools package."
        )

    # Validate OS/arch before unzipping anything.
    for zip_path in [p for p in (basic, tools) if p]:
        ok, msg = _check_zip_platform(zip_path)
        if not ok:
            ui.error(msg)
            return 3

    # Extract.
    target_dir: Optional[str] = None
    for zip_path in [p for p in (basic, tools) if p]:
        top = _zip_top_dir(zip_path)
        ui.info(f"Extracting {os.path.basename(zip_path)} ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(search_dir)
        if top:
            target_dir = os.path.join(search_dir, top)

    if not target_dir or not os.path.isdir(target_dir):
        # Fall back to any instantclient_* folder.
        candidates = sorted(glob.glob(os.path.join(search_dir, "instantclient_*")))
        target_dir = candidates[-1] if candidates else None

    if not target_dir:
        ui.error("Extraction finished but no 'instantclient_*' folder was found.")
        return 4
    target_dir = os.path.abspath(target_dir)

    # Verify the extracted folder is usable.
    if not _has_oci_lib(target_dir):
        ui.error(f"Extracted to {target_dir} but no OCI library ({', '.join(oci_lib_names())}) is there.")
        return 5
    ui.ok(f"OCI client library present in {target_dir}")

    have_dp = all(
        os.path.isfile(os.path.join(target_dir, _dp_exe(n))) for n in ("expdp", "impdp")
    )
    if have_dp:
        ui.ok("expdp / impdp present (export/import/clone enabled).")
    else:
        ui.warn("expdp / impdp NOT present (add the tools package for Data Pump).")

    # Wire up the config.
    _ensure_config(config_path)
    updates = {"oracle_client_lib_dir": target_dir,
               "datapump_bin_dir": target_dir if have_dp else ""}
    written = set_top_level_keys(_config_target(config_path), updates)
    ui.ok(f"Updated {written}:")
    ui.info(f"  oracle_client_lib_dir = {target_dir}")
    ui.info(f"  datapump_bin_dir      = {target_dir if have_dp else '(empty)'}")
    return 0


# ---------------------------------------------------------------------------
# Shared detection (doctor + wire-path)
# ---------------------------------------------------------------------------
def _candidate_bins() -> List[str]:
    """Folders likely to hold Oracle tools, in priority order."""
    dirs: List[str] = []
    oracle_home = os.environ.get("ORACLE_HOME")
    if oracle_home:
        dirs.append(os.path.join(oracle_home, "bin"))
        dirs.append(oracle_home)
    # Whatever is already on PATH is handled by shutil.which separately.
    return [d for d in dirs if os.path.isdir(d)]


def _locate(tool: str) -> Optional[str]:
    found = shutil.which(tool)
    if found:
        return found
    exe = _dp_exe(tool)
    for d in _candidate_bins():
        path = os.path.join(d, exe)
        if os.path.isfile(path):
            return path
    return None


def _listener_up(host: str = "localhost", port: int = 1521) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@dataclass
class Diagnosis:
    """Structured result of probing the machine for Oracle tooling."""

    os_name: str
    arch: str
    oracle_home: Optional[str]
    tools: Dict[str, Optional[str]] = field(default_factory=dict)  # name -> path|None
    lib_dir: Optional[str] = None
    bin_dir: Optional[str] = None
    listener: bool = False

    @property
    def datapump_ok(self) -> bool:
        return bool(self.tools.get("expdp") and self.tools.get("impdp"))

    @property
    def oci_ok(self) -> bool:
        return bool(self.lib_dir)

    @property
    def wire_ready(self) -> bool:
        """True only when everything wire-path needs is resolvable."""
        return self.datapump_ok and self.oci_ok

    def missing(self) -> List[str]:
        gaps: List[str] = []
        for tool in ("expdp", "impdp"):
            if not self.tools.get(tool):
                gaps.append(tool)
        if not self.oci_ok:
            gaps.append(f"OCI client lib ({', '.join(oci_lib_names())})")
        return gaps


def diagnose() -> Diagnosis:
    """Probe PATH + ORACLE_HOME for Oracle tooling. Pure detection, no side effects."""
    oracle_home = os.environ.get("ORACLE_HOME")
    tools = ("sqlplus", "expdp", "impdp")
    found: Dict[str, Optional[str]] = {t: _locate(t) for t in tools}

    bin_dir: Optional[str] = None
    for tool in ("expdp", "impdp"):
        if found[tool]:
            bin_dir = os.path.dirname(found[tool])
            break

    lib_dir: Optional[str] = None
    search_lib: List[str] = [os.path.dirname(p) for p in found.values() if p]
    if oracle_home:
        search_lib += [
            os.path.join(oracle_home, "bin"),
            os.path.join(oracle_home, "lib"),
            oracle_home,
        ]
    for d in search_lib:
        if _has_oci_lib(d):
            lib_dir = os.path.abspath(d)
            break

    return Diagnosis(
        os_name=os_token(),
        arch=arch_token(),
        oracle_home=oracle_home,
        tools=found,
        lib_dir=lib_dir,
        bin_dir=bin_dir,
        listener=_listener_up(),
    )


def _print_report(diag: Diagnosis) -> None:
    ui.info(f"This machine: {diag.os_name}/{diag.arch}")
    ui.info(f"ORACLE_HOME = {diag.oracle_home or '(not set)'}")
    print()
    ui.info("Tool check:")
    for tool in ("sqlplus", "expdp", "impdp"):
        path = diag.tools.get(tool)
        mark = "OK " if path else "-- "
        print(f"   [{mark}] {tool:<8} {path or 'NOT FOUND'}")
    print(f"   [{'OK ' if diag.oci_ok else '-- '}] {'ocilib':<8} {diag.lib_dir or 'NOT FOUND'}")
    print(f"   [{'OK ' if diag.listener else '-- '}] {'listener':<8} "
          f"{'localhost:1521 reachable' if diag.listener else 'no listener on localhost:1521'}")
    print()


def doctor() -> int:
    """Diagnostic only: report Oracle tooling health. Never touches config/PATH."""
    ui.header("Doctor: Oracle tooling health check")
    diag = diagnose()
    _print_report(diag)

    if diag.wire_ready:
        ui.ok("Healthy: expdp, impdp and the OCI client lib are all resolvable.")
        if not diag.listener:
            ui.warn("No listener on localhost:1521 (start your local DB before connecting).")
        ui.info("Run 'wire-path' to point connections.yaml at these system tools.")
        return 0

    ui.warn("Not fully healthy. Missing: " + ", ".join(diag.missing()))
    ui.info("Fix the PATH / system install, or use 'extract-client' for a bundled client.")
    return 1


def wire_path(config_path: Optional[str]) -> int:
    """Wire connections.yaml to PATH/system-resolved tools, but only if healthy."""
    ui.header("Wire connections.yaml to system Oracle (PATH)")
    diag = diagnose()
    _print_report(diag)

    if not diag.wire_ready:
        ui.error("Refusing to wire: required tooling is missing.")
        ui.info("Missing: " + ", ".join(diag.missing()))
        ui.info("Resolve these on PATH / via ORACLE_HOME, or use 'extract-client'.")
        return 1

    updates = {
        "oracle_client_lib_dir": diag.lib_dir or "",
        "datapump_bin_dir": diag.bin_dir or "",
    }
    _ensure_config(config_path)
    written = set_top_level_keys(_config_target(config_path), updates)
    ui.ok(f"Wired {written} to system Oracle:")
    ui.info(f"  oracle_client_lib_dir = {updates['oracle_client_lib_dir']}")
    ui.info(f"  datapump_bin_dir      = {updates['datapump_bin_dir']}")
    return 0


# ---------------------------------------------------------------------------
# config plumbing
# ---------------------------------------------------------------------------
def _config_target(config_path: Optional[str]) -> str:
    return config_path or "connections.yaml"


def _ensure_config(config_path: Optional[str]) -> None:
    target = _config_target(config_path)
    if os.path.isfile(target):
        return
    if os.path.isfile("connections.example.yaml"):
        shutil.copyfile("connections.example.yaml", target)
        ui.info(f"Created {target} from connections.example.yaml.")
    else:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("connections: []\n")
        ui.warn(f"Created an empty {target}; add your connections.")
