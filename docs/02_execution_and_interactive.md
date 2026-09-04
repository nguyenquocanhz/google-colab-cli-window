---
log:
2026-09-04: Pinned `encoding="utf-8"` on every text-mode `open()` in the package. Without it Python uses the locale codec -- UTF-8 on Linux, cp1252 on Windows -- so `colab exec -f script.py` raised `UnicodeDecodeError` on any script containing a non-ASCII character, before a byte was sent to the VM (`commands/execution.py:191`). Four more sites had the same latent bug: `auth.py` reading the OAuth client config and **writing `token.json`**, and `state.py` reading and appending session state -- all JSON that can legally carry non-ASCII. Added `test_no_text_open_without_encoding`, which scans every entry of `colab_cli.__path__` rather than just the first: a non-editable install leaves a stale copy under `.venv` alongside `src`, and a single-root version of the guard passed vacuously while auditing that copy (confirmed by removing `encoding=` from `state.py` and watching it stay green). The matcher uses a `(?<![\w.])` lookbehind so `webbrowser.open` and `urlopen` are not flagged.
2026-09-04: Made `colab console` importable and runnable on Windows. `console.py` imported `termios` and `tty` unconditionally at module scope; both are POSIX-only, so `ModuleNotFoundError: No module named 'termios'` was raised while merely loading `colab_cli.cli` (via `commands/execution.py` -> `from colab_cli.console import connect_console`). Every subcommand died, including `colab --version` and `colab sessions` -- the CLI was unusable on Windows rather than degraded. The imports are now guarded and a module-level `_CO_TTY_POSIX` flag gates both `is_tty` sites, so Windows takes the already-supported piped-stdin path instead: no `termios.tcgetattr`, no `tty.setraw`, and no `signal.SIGWINCH` (which does not exist on Windows and would have raised `AttributeError` even with the imports fixed). Interactive raw-mode `console`/`repl` remain POSIX-only; `exec`, `run`, `upload`, `download` and session management work. Verified by loading the CLI and running `colab --help` on Windows 10 19045 / Python 3.14, where both failed before the change.
2026-05-07: Fixed `colab console` piped-stdin handling. Previously a piped invocation (e.g. `echo 'cmd' | colab console -s s`) sent the command and then hung indefinitely because the previous EOF handler emitted a bare `\x04` (Ctrl-D), which the remote `tmux`-wrapped bash treats as a literal character rather than a session terminator. The new handler sends `exit\n` (which bash actually exits on) and then closes the websocket from the client side after a short grace period (`PIPED_EOF_GRACE_SECONDS = 0.5s`) so any tail output (bash `logout`, tmux `[exited]`) makes it back to the user. TTY mode is unchanged: real-terminal EOF is left to the remote shell. Verified live: `echo 'echo HELLO' | colab console -s s` now exits in ~1.2s instead of hanging.

2026-05-07: Fixed `print_kitty` (used by `colab exec --output-image` and any image-producing exec) to no-op when `sys.stdout.isatty()` is false. The Kitty Graphics Protocol escape sequence is meaningless when stdout is a file or pipe and was visually corrupting captured output (a multi-KB base64 PNG blob would land in log files, grep targets, or showboat captures). Image bytes are still saved to disk via `handle_image`'s file-write path; only the inline-render attempt is suppressed.

2026-06-04: Bumped the default `--timeout` for `colab exec` from 10s to 30s (and the matching `colab run` default) so brief silent tasks are less likely to hit a premature `TimeoutError`. Explicit `--timeout` overrides are unaffected.
---

# Design: Execution and Interactive Interaction (`repl`, `exec`, `console`)

## Overview
Execution involves sending Python code (or shell commands) to the Jupyter kernel running on the Colab VM and processing the stream of output messages.

## Approach

### 1. REPL (`colab repl`)
- **Transport**: WebSockets (using `websockets` library if allowed, or a custom `http.client` based long-polling implementation if we're strictly stdlib).
- **Communication**: Jupyter Kernel Messaging Protocol.
    - `execute_request`: Send code string.
    - `execute_reply`: Get status.
    - `iopub.stream`: Capture `stdout` and `stderr`.
- **Interactive Mode**: Standard Python `cmd.Cmd` or `code.InteractiveConsole` for local input/output.
- **Piping Support**: Detect `sys.stdin.isatty()`. If not a TTY, read all input and send as a single execution request.

### 2. Execution (`colab exec`)
- **File Handling**:
    - If file path is local: Read content, send as code.
    - If file path is remote: Execute `!python <path>`.
- **Multi-Modal Output**: Handle `display_data` messages (e.g., `image/png`, `text/html`). For the CLI, we'll save images to temporary files and print their paths, or if the terminal supports it (e.g., iTerm2), inline them.
- **Timeout Configuration**: Exposes a `--timeout` flag (default 30s) to allow long-running silent tasks (like model compilation or data downloading) to execute without being prematurely killed.

### 3. Console (`colab console`)
- **Implementation**: Connects directly to the backend terminal endpoint (`/colab/tty`) via WebSockets using `websocket-client`.
- **Interactive**: Bypasses the Jupyter kernel entirely to provide a raw, PTY-backed bash session on the Colab VM.
- **Platform**: Raw-mode interactivity is POSIX-only. `termios` and `tty` are
  imported defensively and `_CO_TTY_POSIX` gates both `is_tty` checks, so on
  Windows the client falls back to the piped-stdin path described below rather
  than failing to import. `signal.SIGWINCH` is likewise POSIX-only and is only
  installed on that path.
- **Terminal Management** (POSIX): Configures `sys.stdin` to raw mode using `termios` and `tty`, passing single characters to the socket and writing raw ANSI escape sequences directly to `sys.stdout.buffer`. Hooks into `SIGWINCH` to communicate local terminal dimensions (`cols`/`rows`) to the remote bash environment so output rendering works perfectly during resizing.
- **Piped stdin**: Detected via `sys.stdin.isatty()`. When piped, the input characters are forwarded one at a time to the remote pty, and on EOF the client sends `exit\n` and then closes the websocket itself after `PIPED_EOF_GRACE_SECONDS` (0.5s) so the user's shell goodbye text drains back. The remote `/colab/tty` endpoint wraps bash in tmux, which intercepts a bare `\x04` as a literal character — that is why we send `exit\n` rather than Ctrl-D.

## Implementation Details
- **Kernel Management**: `ColabRuntime` (from `colab-agent`) already handles message signing and message types.
- **Output Streaming**: Continuous polling or asynchronous message handling to provide real-time output.
- **Piping Example**: `cat script.py | colab exec -s my-session`.

## Testing Strategy
TDD is mandatory for all execution features.

### 1. Mock Kernel Client
- **Test Case**: Verify `ColabRuntime` correctly sends an `execute_request` message over the websocket.
- **Test Case**: Verify `iopub.stream` messages are correctly handled and printed to `stdout` in real-time.
- **Test Case**: Verify `display_data` (specifically `image/png`) triggers the correct local handling (saving or display).

### 2. TTY and Piping
- **Test Case**: Mock `sys.stdin.isatty()` to verify `colab repl` correctly switches between interactive mode and one-shot piped execution.
- **Test Case**: Verify large piped inputs are handled without buffer overflow or truncation.
- **Test Case**: `colab console` with piped stdin sends `exit\n` and calls `ws.close()` on EOF (regression: previously sent `\x04` only and hung).
- **Test Case**: `colab console` in TTY mode does not synthesize an exit on EOF (the user owns the session lifecycle).
- **Test Case**: `print_kitty` is a no-op when `sys.stdout.isatty()` is false (regression: previously emitted ANSI/base64 into pipes and files).
