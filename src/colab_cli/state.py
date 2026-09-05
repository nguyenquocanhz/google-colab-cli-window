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

import contextlib
import json
import os
from datetime import datetime
from typing import Dict, Optional, Tuple, Iterator, IO

import filelock
from pydantic import BaseModel

from colab_cli.paths import config_home


class SessionState(BaseModel):
    name: str
    token: str
    url: str
    endpoint: str
    variant: str = "DEFAULT"
    accelerator: str = "NONE"
    machine_shape: str = "STANDARD"
    kernel_id: Optional[str] = None
    session_id: Optional[str] = None
    last_execution: Optional[Tuple[str, Optional[str], str]] = None
    running: Optional[str] = None
    keep_alive_pid: Optional[int] = None


class Settings(BaseModel):
    update_url: str = "https://pypi.org/pypi/google-colab-cli/json"
    last_check: Optional[datetime] = None
    enable_update_check: bool = True
    # Highest version seen on the update source; cached for the banner.
    latest_version: Optional[str] = None


class _LockedFileStore:
    def __init__(self, path: str):
        self.path = path
        self.lock_path = "%s.lock" % self.path
        # ReadWriteLock gives us shared (concurrent) readers and exclusive
        # writers -- the cross-platform equivalent of fcntl LOCK_SH/LOCK_EX.
        # is_singleton=False keeps each store's lock independent: with the
        # default (True), two StateStore instances for the same path in one
        # process are merged into a single reentrant lock, whose reentrancy
        # guard then raises RuntimeError when two threads contend for the write
        # lock. We want them to actually serialize via the underlying file lock.
        self._rwlock = filelock.ReadWriteLock(self.lock_path, is_singleton=False)
        self._ensure_dir()

    def _ensure_dir(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _write_data(self, f: IO, data: str):
        # Write to a sibling temp file, fsync it, then rename over the target.
        # `seek(0); truncate(); write()` leaves a window where the file is
        # EMPTY, so any death in between -- SIGKILL, power loss, a killed
        # wrapper process -- destroys every session name on disk while the
        # runtimes keep running on the server, unreachable and still counted
        # against quota. `os.replace` is atomic on POSIX and on Windows, so a
        # reader sees either the old content or the new, never nothing.
        #
        # CLOSE `f` FIRST -- on Windows the replace fails otherwise.
        #
        # `_lock_exclusive` opens the target and hands the caller `f`, which
        # the caller reads through `_load_raw` before writing. Windows refuses
        # to rename over a file that still has an open handle unless every
        # handle was opened with FILE_SHARE_DELETE, and `open()` does not ask
        # for it -- so `os.replace` raised `PermissionError: [WinError 5]` and
        # the store could not be written AT ALL. Not a corner case: every
        # `colab new` failed to record its session, which is the orphaned
        # runtime this atomic write was added to prevent, arrived at by
        # another road. POSIX allows the rename over an open handle, which is
        # why the original version passed there and the Windows suite went
        # from 17 passing to 11 failing in `test_state.py` alone.
        #
        # Closing early is safe: `f` exists only to be READ (the lock lives in
        # a separate `.lock` file, so the handle carries no locking duty since
        # the move off `fcntl`), every caller has finished with it by the time
        # it writes, and the `with open(...)` in `_lock_exclusive` closing an
        # already-closed file is a no-op. The exclusive lock is still held
        # throughout, so no other process can slip in between.
        self._ensure_dir()
        tam = "%s.tmp%d" % (self.path, os.getpid())
        try:
            with open(tam, "w", encoding="utf-8") as g:
                g.write(data)
                g.flush()
                os.fsync(g.fileno())
            try:
                f.close()
            except (OSError, ValueError):
                pass
            os.replace(tam, self.path)
        except BaseException:
            try:
                os.unlink(tam)
            except OSError:
                pass
            raise

    @contextlib.contextmanager
    def _lock_shared(self) -> Iterator[Optional[IO]]:
        if not os.path.exists(self.path):
            yield None
            return
        with self._rwlock.read_lock():
            with open(self.path, "r", encoding="utf-8") as f:
                yield f

    @contextlib.contextmanager
    def _lock_exclusive(self) -> Iterator[IO]:
        with self._rwlock.write_lock():
            with open(self.path, "a+", encoding="utf-8") as f:
                yield f


class SettingsStore(_LockedFileStore):
    def __init__(self, path: Optional[str] = None):
        if not path:
            path = os.path.join(config_home(), "settings.json")
        super().__init__(path)

    def load(self) -> Settings:
        with self._lock_shared() as f:
            if f is None:
                return Settings()
            try:
                content = f.read()
                if not content or content.isspace():
                    return Settings()
                data = json.loads(content)
                return Settings.model_validate(data)
            except Exception:
                return Settings()

    def save(self, settings: Settings):
        with self._lock_exclusive() as f:
            self._write_data(f, settings.model_dump_json(indent=2))


class StateStore(_LockedFileStore):
    def __init__(self, path: Optional[str] = None):
        if not path:
            # Same directory as the credentials (see `paths.config_home`),
            # so an account switch moves sessions and token together.
            # Moving one
            # without the other leaves session names pointing at runtimes the
            # current credentials cannot reach.
            path = os.path.join(config_home(), "sessions.json")
        super().__init__(path)

    def _load_raw(self, f) -> Dict[str, SessionState]:
        try:
            f.seek(0)
            content = f.read()
            if not content or content.isspace():
                return {}
            data = json.loads(content)
            return {k: SessionState(**v) for k, v in data.items()}
        except Exception:
            return {}

    def _save_raw(self, f, sessions: Dict[str, SessionState]):
        content = json.dumps({k: v.model_dump() for k, v in sessions.items()}, indent=2)
        self._write_data(f, content)

    def add(self, state: SessionState):
        with self._lock_exclusive() as f:
            sessions = self._load_raw(f)
            sessions[state.name] = state
            self._save_raw(f, sessions)

    def get(self, name: str) -> Optional[SessionState]:
        with self._lock_shared() as f:
            if f is None:
                return None
            sessions = self._load_raw(f)
            return sessions.get(name)

    def remove(self, name: str):
        with self._lock_exclusive() as f:
            sessions = self._load_raw(f)
            if name in sessions:
                del sessions[name]
                self._save_raw(f, sessions)

    def list(self) -> Dict[str, SessionState]:
        with self._lock_shared() as f:
            if f is None:
                return {}
            return self._load_raw(f)
