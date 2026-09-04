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

"""Regression tests for POSIX-only imports leaking into the Windows path.

`console.py` used to `import termios` (and `tty`) unconditionally at module
scope. Because `commands/execution.py` does `from colab_cli.console import
connect_console`, that import ran while merely loading `colab_cli.cli` -- so on
Windows *every* subcommand died with `ModuleNotFoundError: No module named
'termios'`, including `colab --version`.

These tests reproduce that failure mode on any platform by hiding the modules,
so the regression is caught in CI on Linux rather than only by Windows users.
"""

import builtins
import importlib
import sys

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
    pass the tests above on Linux (where the module exists) unless the fixture
    happened to be used, so assert on the text as well.
    """
    import colab_cli.console as console

    with open(console.__file__, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    offenders = [
        (n, line)
        for n, line in enumerate(lines, 1)
        # column 0 == module scope; guarded imports are indented inside `try:`
        if any(line == f"import {mod}" for mod in POSIX_ONLY)
    ]
    assert not offenders, (
        "POSIX-only modules imported unguarded at module scope: "
        + ", ".join(f"line {n}: {line!r}" for n, line in offenders)
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
