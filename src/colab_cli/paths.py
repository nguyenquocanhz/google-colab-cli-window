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

"""Filesystem locations for this CLI's per-account state.

Its own module, and one that imports nothing but `os`, so that every module
holding state -- `auth`, `state`, `history`, `common` -- can ask the same
question without importing each other. Putting the helper in `auth` instead
would have made `history` pull in google-auth just to build a path.
"""

import os


def config_home() -> str:
    """Directory holding this CLI's credentials, session state and logs.

    Overridable with `COLAB_CLI_HOME`, defaulting to the previous hardcoded
    location so nothing changes for existing users.

    Why this needs to be a variable: `token.json` was pinned to one absolute
    path while `--config` moved only `sessions.json`. That made it impossible
    to keep more than one Google account side by side -- a real need for
    anyone with separate personal and work accounts, and the reason `gcloud`
    ships `config configurations`.

    Splitting only half the state is worse than not splitting at all: the
    sessions would follow one account while the credentials silently stayed
    with another, and session names would point at runtimes the current
    credentials cannot reach. So everything the CLI writes per account --
    `token.json`, `sessions.json`, `settings.json`, `colab.log` and
    `history/` -- resolves through this one function.

    Read at import time by `auth.TOKEN_CONFIG_PATH`, so `COLAB_CLI_HOME` has
    to be set in the environment before the process starts. That is the
    normal case (a shell export, or a parent process building the child's
    env); changing it mid-process will not move the token path.
    """
    return os.path.expanduser(
        os.environ.get("COLAB_CLI_HOME", "~/.config/colab-cli"))
