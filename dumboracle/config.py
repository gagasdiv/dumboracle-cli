"""Load and validate the YAML connection config."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml


class ConfigError(Exception):
    pass


@dataclass
class Connection:
    name: str
    dsn: str
    username: str
    password: str
    description: str = ""
    sysdba: bool = False
    alias: str = ""

    def matches_alias(self, token: str) -> bool:
        """Case-insensitive match against this connection's alias."""
        return bool(self.alias) and self.alias.strip().lower() == token.strip().lower()

    def header_line(self) -> str:
        """Single-line summary used as the operations-menu header."""
        markers = ""
        if self.alias:
            markers += f"  [alias: {self.alias}]"
        if self.sysdba:
            markers += "  [SYSDBA]"
        return f"{self.name}  ({self.username}@{self.dsn}){markers}"

    def label(self) -> str:
        extra = " [SYSDBA]" if self.sysdba else ""
        alias = f" [alias: {self.alias}]" if self.alias else ""
        desc = f" - {self.description}" if self.description else ""
        return f"{self.name}{alias}{extra} ({self.username}@{self.dsn}){desc}"


@dataclass
class Config:
    connections: List[Connection] = field(default_factory=list)
    oracle_client_lib_dir: Optional[str] = None
    datapump_bin_dir: Optional[str] = None


DEFAULT_PATHS = ("connections.yaml", "connections.yml")


def find_config_path(explicit: Optional[str]) -> str:
    if explicit:
        if not os.path.isfile(explicit):
            raise ConfigError(f"Config file not found: {explicit}")
        return explicit
    for candidate in DEFAULT_PATHS:
        if os.path.isfile(candidate):
            return candidate
    raise ConfigError(
        "No config file found. Create 'connections.yaml' "
        "(see connections.example.yaml) or pass --config <path>."
    )


def load_config(path: Optional[str] = None) -> Config:
    resolved = find_config_path(path)
    with open(resolved, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping.")

    conns_raw = raw.get("connections") or []
    if not isinstance(conns_raw, list) or not conns_raw:
        raise ConfigError("Config must define a non-empty 'connections' list.")

    connections: List[Connection] = []
    for i, item in enumerate(conns_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"connections[{i}] must be a mapping.")
        try:
            connections.append(
                Connection(
                    name=str(item["name"]),
                    dsn=str(item["dsn"]),
                    username=str(item["username"]),
                    password=str(item["password"]),
                    description=str(item.get("description", "")),
                    sysdba=bool(item.get("sysdba", False)),
                    alias=str(item.get("alias", "")).strip(),
                )
            )
        except KeyError as exc:
            raise ConfigError(
                f"connections[{i}] missing required key: {exc}"
            ) from exc

    def clean(value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return Config(
        connections=connections,
        oracle_client_lib_dir=clean(raw.get("oracle_client_lib_dir")),
        datapump_bin_dir=clean(raw.get("datapump_bin_dir")),
    )


def _yaml_quote(value: str) -> str:
    """Single-quoted YAML scalar (backslashes are literal; '' escapes a quote)."""
    if value == "":
        return '""'
    return "'" + value.replace("'", "''") + "'"


def set_top_level_keys(path: str, updates: Dict[str, str]) -> str:
    """Update/insert top-level scalar keys in a YAML file, keeping comments.

    Only lines where the key sits at column 0 are touched, so nested keys inside
    ``connections:`` are never matched. Missing keys are inserted at the top.
    Returns the path written, for convenient logging.
    """
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    else:
        lines = []

    remaining = dict(updates)
    for i, line in enumerate(lines):
        for key in list(remaining):
            if re.match(rf"^{re.escape(key)}\s*:", line):
                lines[i] = f"{key}: {_yaml_quote(remaining.pop(key))}"
                break

    # Insert any keys that weren't already present, at the very top.
    if remaining:
        inserted = [f"{k}: {_yaml_quote(v)}" for k, v in remaining.items()]
        lines = inserted + lines

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
