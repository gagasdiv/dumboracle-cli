"""Terminal prompts and a couple of tiny formatting helpers.

Everything here is intentionally dependency-free and forgiving: EOF / Ctrl-C
on a prompt is turned into a clean ``Abort`` instead of a crash.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


class Abort(Exception):
    """Raised when the user backs out of a prompt (blank / Ctrl-C)."""


def _read(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        raise Abort() from None


def read_line(prompt: str) -> str:
    """Public single-line reader. Raises Abort on EOF / Ctrl-C."""
    return _read(prompt)


def rule(char: str = "-", width: int = 64) -> None:
    print(char * width)


def header(title: str) -> None:
    print()
    rule("=")
    print(f"  {title}")
    rule("=")


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"  [ok] {msg}")


def warn(msg: str) -> None:
    print(f"  [!] {msg}")


def error(msg: str) -> None:
    print(f"  [ERROR] {msg}")


def ask(prompt: str, default: Optional[str] = None, allow_blank: bool = False) -> str:
    """Ask for a line of text.

    If ``default`` is given, an empty answer returns it. Otherwise an empty
    answer aborts unless ``allow_blank`` is set.
    """
    suffix = f" [{default}]" if default is not None else ""
    value = _read(f"  {prompt}{suffix}: ").strip()
    if not value:
        if default is not None:
            return default
        if allow_blank:
            return ""
        raise Abort()
    return value


def ask_secret(prompt: str, default: Optional[str] = None) -> str:
    """Ask for a password. Falls back to visible input where getpass can't run."""
    import getpass

    suffix = f" [{default}]" if default is not None else ""
    try:
        value = getpass.getpass(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Abort() from None
    if not value and default is not None:
        return default
    return value


def confirm(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    value = _read(f"  {prompt} ({hint}): ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


def choose(title: str, options: Sequence[Tuple[str, str]], back_label: str = "Back") -> Optional[str]:
    """Render a numbered menu and return the chosen option key, or None for back.

    ``options`` is a sequence of ``(key, label)`` pairs.
    """
    header(title)
    for idx, (_, label) in enumerate(options, start=1):
        print(f"   {idx}. {label}")
    print(f"   0. {back_label}")
    print()
    while True:
        raw = _read("  Select: ").strip()
        if raw in ("0", ""):
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1][0]
        warn("Invalid selection, try again.")


def choose_from(title: str, items: List[str], back_label: str = "Cancel") -> Optional[str]:
    """Choose a raw string value from a list."""
    opts = [(item, item) for item in items]
    return choose(title, opts, back_label=back_label)
