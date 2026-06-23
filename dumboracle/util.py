"""Small shared helpers: timestamps, identifier normalisation and quoting."""

from __future__ import annotations

from datetime import datetime


def timestamp() -> str:
    """Return a stamp like ``20260623-2010`` (yyyymmdd-hhii)."""
    return datetime.now().strftime("%Y%m%d-%H%M")


def stamped_name(base: str) -> str:
    """``BNI_EIM`` -> ``BNI_EIM--20260623-2010`` (normalised, uppercased)."""
    return f"{norm_ident(base)}--{timestamp()}"


def norm_ident(name: str) -> str:
    """Normalise a user-supplied identifier.

    Oracle treats unquoted identifiers as upper-case, so we upper-case here to
    match what is actually stored in the dictionary. Whitespace is trimmed.
    """
    return name.strip().strip('"').upper()


def q(name: str) -> str:
    """Return a safely double-quoted Oracle identifier.

    Quoting lets us use names that contain characters like ``-`` (used in the
    ``<user>--yyyymmdd-hhii`` convention) without Oracle complaining.
    """
    return '"' + name.replace('"', '""') + '"'


def qlit(value: str) -> str:
    """Return a single-quoted Oracle string literal."""
    return "'" + value.replace("'", "''") + "'"
