Windows-compatibility build of `googlecolab/google-colab-cli`, at upstream
`465b941` plus the two fixes in this fork.

## Why this fork exists

Upstream is unusable on Windows, not merely degraded:

- `console.py` imported `termios` and `tty` at module scope. `commands/execution.py`
  pulls that in while loading `colab_cli.cli`, so **every** subcommand died with
  `ModuleNotFoundError: No module named 'termios'` — including `colab --version`.
- `open(file, "r")` without `encoding=` uses the locale codec (cp1252 on Windows),
  so `colab exec -f script.py` raised `UnicodeDecodeError` on any script with a
  non-ASCII character. Four more sites had the same latent bug, including
  `auth.py` **writing `token.json`**.

Fixed in 3d1884a and 11c0f6a. Design docs under `docs/` carry dated log entries.

## What is in this release

| File | What it is |
|---|---|
| `colab-windows-x64.exe` | Standalone. PyInstaller one-file, no Python needed. ~71 MB. |
| `*.whl` | Normal install: `pip install <file>.whl` |
| `*.tar.gz` | Source distribution |
| `ColabCLI-windows-0.6.1.msi` | Per-user installer. No admin, no UAC. Adds `colab` to your user PATH. |
| `SHA256SUMS.txt` | Checksums |

The MSI installs to `%LOCALAPPDATA%\Programs\Colab CLI`. It was verified by
installing silently, running the installed binary, and uninstalling: exit code 0
both ways, the directory removed, the PATH entry withdrawn. Per-user on purpose —
an unsigned MSI that asks for elevation is a thing users should be suspicious of.

## Verified on Windows 10 19045 / Python 3.14

- `colab version` → `0.1.dev53+g11c0f6af8`
- `colab --auth=oauth2 sessions` → listed a live T4 session (real auth + network)
- `colab exec -f <file with Vietnamese text>` → reads the file, no `UnicodeDecodeError`
- Full end-to-end run: `new --gpu T4` → `upload` → `exec` of a training job on a
  Tesla T4, output streamed back
- `uv run pytest tests/` → 322 passed, 1 skipped. On upstream, three test modules
  failed to collect at all on Windows.
- `uv run ruff check .` → the same 5 pre-existing findings as upstream

## Known gaps

- **Not code-signed.** SmartScreen will warn on first run. Verify the SHA256 above.
- Interactive raw-mode `console` and `repl` remain POSIX-only by design.
- 21 tests still fail on Windows for reasons unrelated to these fixes, previously
  masked by the collection error: `test_repl` (11, prompt_toolkit Windows output),
  `test_ssh_lifecycle` (4) and `test_ssh_autocreate` (2, POSIX signal handling),
  `test_cli` (4).
- The CLI's *logger* still writes non-ASCII to a cp1252 stream and emits a noisy
  `UnicodeEncodeError` traceback. It does not stop the command. Not yet fixed.

Apache-2.0, same as upstream. Changes are stated per section 4(b).
