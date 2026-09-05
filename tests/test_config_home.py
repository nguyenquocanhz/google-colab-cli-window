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

"""`COLAB_CLI_HOME` has to move ALL of the per-account state, or none of it.

`--config` already moved `sessions.json`, and that was the bug: `token.json`
stayed pinned to one absolute path, so pointing `--config` at a second account
gave you that account's session names authenticated as the first account's
user. Session names then referred to runtimes the credentials could not reach,
and the failure surfaced far from its cause.

So the guard here is not "the override works" -- it is "nothing was left
behind". The scan at the bottom is the part that earns its keep: the first
cut of this change moved the token, the sessions and the settings, and quietly
left `history/` and the update hint pointing at the shared directory.
"""

import importlib
import os
import pathlib
import re

import pytest

from colab_cli.paths import config_home


DEFAULT = "~/.config/colab-cli"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point `COLAB_CLI_HOME` at a scratch directory for one test."""
    monkeypatch.setenv("COLAB_CLI_HOME", str(tmp_path))
    return tmp_path


def test_default_is_the_previous_hardcoded_location(monkeypatch):
    """Existing installs must not notice this change."""
    monkeypatch.delenv("COLAB_CLI_HOME", raising=False)
    assert config_home() == os.path.expanduser(DEFAULT)


def test_override_is_honored(home):
    assert config_home() == str(home)


def test_override_expands_a_tilde(monkeypatch):
    """`COLAB_CLI_HOME=~/accounts/work` is the natural thing to type."""
    monkeypatch.setenv("COLAB_CLI_HOME", "~/accounts/work")
    assert config_home() == os.path.expanduser("~/accounts/work")


def test_token_follows_the_override(home):
    """`auth.TOKEN_CONFIG_PATH` is a module constant, so it is fixed at import.

    That is deliberate -- the CLI reads it once per process and the variable is
    set before launch -- but it means this test has to reload the module rather
    than just setting the environment.
    """
    import colab_cli.auth

    auth = importlib.reload(colab_cli.auth)
    try:
        assert auth.TOKEN_CONFIG_PATH == os.path.join(str(home), "token.json")
    finally:
        # Leave the module matching the ambient environment again, or every
        # later test in the session inherits this tmp_path.
        monkey = os.environ.pop("COLAB_CLI_HOME", None)
        importlib.reload(colab_cli.auth)
        if monkey is not None:
            os.environ["COLAB_CLI_HOME"] = monkey


def test_history_follows_the_override(home):
    """The command history is per account too.

    It is keyed by session name, and session names collide across accounts, so
    a shared history directory does not merely mix two logs -- it interleaves
    two accounts' events inside one file that looks like a single timeline.
    """
    from colab_cli.history import HistoryLogger

    assert HistoryLogger().log_dir == str(home / "history")


def test_explicit_history_dir_still_wins(tmp_path, home):
    """The override is a default, not an override of the caller's argument."""
    from colab_cli.history import HistoryLogger

    other = tmp_path / "somewhere-else"
    assert HistoryLogger(str(other)).log_dir == str(other)


def _package_roots():
    """Every directory `colab_cli` resolves from -- see test_windows_compat."""
    import colab_cli

    return [pathlib.Path(p) for p in colab_cli.__path__]


# `~/.config/colab-cli` written as a literal, anywhere -- not just where a
# quote starts. The first version of this pattern anchored on the quote and so
# missed three help strings that merely mention the path mid-sentence, which is
# precisely where a stale path does its damage: the user reads it and edits or
# deletes a file the CLI will never touch.
_HARDCODED = re.compile(r"~[/\\]\.config[/\\]colab-cli")

# `paths.py` is where the default legitimately lives.
_ALLOWED = {"paths.py"}


def test_no_module_still_hardcodes_the_config_directory():
    """One literal left behind silently splits an account's state in two.

    Help text counts. A hint that names `~/.config/colab-cli/settings.json`
    while the CLI reads somewhere else sends the user to edit a file nothing
    will ever load, and the setting appears not to work.
    """
    offenders = []
    seen = set()
    for root in _package_roots():
        for path in sorted(root.rglob("*.py")):
            if path.resolve() in seen or path.name in _ALLOWED:
                continue
            seen.add(path.resolve())
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if _HARDCODED.search(line):
                    offenders.append(f"{path}:{number}: {line.strip()}")

    assert seen, "scanned no source files -- the guard would pass vacuously"
    assert not offenders, (
        "these bypass colab_cli.paths.config_home(), so COLAB_CLI_HOME will "
        "not move them:\n" + "\n".join(offenders)
    )
