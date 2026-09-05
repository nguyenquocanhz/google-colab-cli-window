# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression tests for platform assumptions that only bite on Windows.

Two families, both of which shipped green through Linux-only CI:

1. POSIX-only imports. `console.py` did `import termios` at module scope, and
   `commands/execution.py` does `from colab_cli.console import connect_console`,
   so the import ran while merely loading `colab_cli.cli`. Every subcommand
   died with `ModuleNotFoundError`, including `colab --version`.

2. Locale-dependent text I/O. `open(path, "r")` without `encoding=` uses the
   locale codec: UTF-8 on Linux, cp1252 on Windows. `colab exec -f script.py`
   raised `UnicodeDecodeError` on any script with a non-ASCII character.

The tests below reproduce both on any platform, so CI on Linux catches them.
"""

import builtins
import contextlib
import importlib
import io
import pathlib
import re
import sys
import tokenize

import pytest

POSIX_ONLY = ("termios", "tty")


@pytest.fixture
def hide_posix_tty(monkeypatch):
    """Make `import termios` / `import tty` raise, as they do on Windows."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in POSIX_ONLY:
            raise ModuleNotFoundError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    for name in POSIX_ONLY:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    yield


def _reload(module_name):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _package_roots():
    """Every directory `colab_cli` resolves from, not just the first.

    A non-editable install puts a *copy* under `.venv/lib/.../colab_cli` while
    `src/colab_cli` stays on `__path__` too, so scanning only `__path__[0]`
    silently audits a stale copy: editing the source changes nothing the test
    can see, and the guard passes vacuously. Verified by removing `encoding=`
    from `state.py` and watching a single-root version of this test still pass.
    """
    import colab_cli

    return [pathlib.Path(p) for p in colab_cli.__path__]


def test_console_imports_without_termios(hide_posix_tty):
    """`colab_cli.console` must import when termios/tty are unavailable."""
    console = _reload("colab_cli.console")
    assert console.termios is None
    assert console.tty is None
    assert console._CO_TTY_POSIX is False


def test_cli_imports_without_termios(hide_posix_tty):
    """The whole CLI must load; this is what actually broke on Windows."""
    _reload("colab_cli.console")
    _reload("colab_cli.commands.execution")
    cli = _reload("colab_cli.cli")
    assert cli.app is not None


def test_console_module_has_no_bare_posix_import():
    """Guard the source itself, so the import cannot silently regress.

    A future edit that reintroduces a top-level `import termios` would still
    pass the tests above on Linux, where the module exists, so assert on the
    text as well. Column 0 means module scope; guarded imports sit indented
    inside a `try:` block.
    """
    import colab_cli.console as console

    lines = pathlib.Path(console.__file__).read_text(encoding="utf-8").splitlines()
    offenders = [
        f"line {n}: {line!r}"
        for n, line in enumerate(lines, 1)
        if any(line == f"import {mod}" for mod in POSIX_ONLY)
    ]
    assert not offenders, (
        "POSIX-only modules imported unguarded at module scope: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("is_a_tty", [True, False])
def test_is_tty_requires_posix_tty(hide_posix_tty, monkeypatch, is_a_tty):
    """On Windows `isatty()` can be True, but raw mode is still impossible.

    `_CO_TTY_POSIX` must veto it either way, otherwise `termios.tcgetattr`
    would be called on `None`.
    """
    console = _reload("colab_cli.console")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: is_a_tty, raising=False)
    assert (console._CO_TTY_POSIX and is_a_tty) is False


# `(?<![\w.])` keeps this to the builtin: `\bopen\(` also matches the `open(`
# inside `webbrowser.open(...)` and `urllib.request.urlopen(...)`, because a
# word boundary sits right after the dot.
_OPEN_CALL = re.compile(r"(?<![\w.])open\(([^)]*)\)")
_BINARY_MODE = re.compile(r"""['"][rwax]\+?b\+?['"]""")


def _bo_chu_thich(text: str) -> str:
    """Blank out `#` comments, keeping line numbers intact.

    The scan is a line regex, so it cannot tell a call from prose about a
    call: a comment explaining why some `open(...)` behaves as it does was
    reported as an offender. Stripping comments narrows the guard to code
    without weakening it -- a real call never lives inside a comment.

    Tokenizing rather than cutting at the first `#`, because a `#` inside a
    string literal is not a comment and blanking from there would corrupt the
    line. A file that will not tokenize is returned unchanged: it is not this
    guard's job to report a syntax error, and scanning the raw text at worst
    over-reports.
    """
    try:
        cac = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    dong = text.splitlines()
    for tok in cac:
        if tok.type != tokenize.COMMENT:
            continue
        hang, cot = tok.start[0] - 1, tok.start[1]
        dong[hang] = dong[hang][:cot]
    return "\n".join(dong)


def test_no_text_open_without_encoding():
    """Text-mode `open()` without `encoding=` picks up the locale codec.

    UTF-8 on Linux, cp1252 on Windows. This broke `colab exec -f` for any
    script containing a non-ASCII character, and `auth.py` wrote `token.json`
    the same way. Binary opens are exempt; so is the `drivemount` keypress
    gate, which opens a raw console device rather than decoded text.
    """
    offenders = []
    seen = set()
    for root in _package_roots():
        for path in sorted(root.rglob("*.py")):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            goc = path.read_text(encoding="utf-8").splitlines()
            # Scan the comment-stripped text, but report the ORIGINAL line --
            # a blanked line would show the reader half a statement.
            quet = _bo_chu_thich("\n".join(goc)).splitlines()
            for number, line in enumerate(quet, 1):
                for match in _OPEN_CALL.finditer(line):
                    args = match.group(1)
                    if "encoding=" in args or _BINARY_MODE.search(args):
                        continue
                    if "_duong" in args:
                        continue
                    offenders.append(f"{path}:{number}: {goc[number - 1].strip()}")
    assert seen, "scanned no source files -- the guard would pass vacuously"
    assert not offenders, (
        "text-mode open() without encoding= is locale-dependent and fails on "
        "Windows:\n" + "\n".join(offenders)
    )


def test_store_write_closes_handle_before_replace(tmp_path, monkeypatch):
    """The atomic store write must close its read handle before renaming.

    `_lock_exclusive` opens the target and hands the caller a handle to read
    through; `_write_data` then renames a temp file over that same target.
    POSIX allows a rename over an open handle, so this passed everywhere CI
    ran -- while on Windows `os.replace` raised
    `PermissionError: [WinError 5]` and the session store could not be
    written AT ALL. Every `colab new` failed to record its session, which is
    precisely the orphaned, quota-holding runtime the atomic write was added
    to prevent.

    Asserted on the handle rather than on the platform, so a regression fails
    on Linux CI too instead of waiting for a Windows user to find it.
    """
    import colab_cli.state as st

    store = st.StateStore(str(tmp_path / "sessions.json"))

    giu = {}
    goc_khoa = store._lock_exclusive

    @contextlib.contextmanager
    def bat_khoa():
        with goc_khoa() as f:
            giu["f"] = f
            yield f

    monkeypatch.setattr(store, "_lock_exclusive", bat_khoa)

    thay = {}
    goc_replace = st.os.replace

    def replace(nguon, dich):
        thay["da_dong"] = giu["f"].closed
        return goc_replace(nguon, dich)

    monkeypatch.setattr(st.os, "replace", replace)
    store.add(st.SessionState(name="s", token="t", url="u", endpoint="e"))

    assert thay.get("da_dong") is True, (
        "os.replace ran while the target still had an open handle; Windows "
        "refuses that rename with WinError 5"
    )
    assert store.get("s") is not None
