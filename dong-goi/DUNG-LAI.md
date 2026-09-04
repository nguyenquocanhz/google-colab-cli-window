# Reproducing the Windows binaries

Both artifacts in the release are built from this directory. Nothing here is
needed to *use* the CLI — `pip install` the wheel and you are done.

## Standalone `colab.exe`

```
uv sync --reinstall-package google-colab-cli        # else PyInstaller bundles a stale copy
uv run --with pyinstaller pyinstaller --noconfirm --onefile --name colab \
    --distpath dist-exe --workpath build-exe --specpath build-exe \
    --collect-all colab_cli --collect-submodules jupyter_kernel_client \
    dong-goi/diem-vao.py
```

The `uv sync` line is not optional. `--collect-all colab_cli` reads the
*installed* package, so a stale `.venv` silently produces a binary from older
source: the first build reported `0.1.dev52` while HEAD was `dev53`, and it
predated the encoding fix. Always confirm with `colab version` afterwards —
the string carries the git short SHA.

## `.msi`

Needs WiX v3 (`candle.exe` / `light.exe`). The binaries zip from
`wixtoolset/wix3` releases is enough; no install, no .NET SDK.

```
candle.exe -dNguonExe=<path>\dist-exe\colab.exe -dNguonGiayPhep=<path>\LICENSE \
           -out colab.wixobj dong-goi/colab.wxs
light.exe -sval -out ColabCLI-windows-0.6.1.msi colab.wixobj
```

`-sval` skips ICE validation, which needs a full Windows Installer SDK.

Two things that will bite:

- **Every user-visible string in `colab.wxs` must be ASCII.** The MSI database
  defaults to code page 1252 and `light` refuses anything outside it with
  `LGHT0311`. `Codepage="65001"` fixes the database but `SummaryCodepage`
  rejects it outright — summary info requires an ANSI page. Keeping the strings
  ASCII and the prose in XML comments avoids the whole argument.
- **Do not declare `ALLUSERS` yourself.** `InstallScope="perUser"` already emits
  it; a manual `Value=""` is rejected because XML has no empty-attribute form.

## Verifying an MSI before shipping it

```
msiexec /i ColabCLI-windows-0.6.1.msi /qn /l*v install.log
"%LOCALAPPDATA%\Programs\Colab CLI\colab.exe" version
msiexec /x ColabCLI-windows-0.6.1.msi /qn
```

Expect exit code 0 both ways, the directory gone afterwards, and the PATH entry
withdrawn. A per-user MSI registers under
`HKCU\Software\Microsoft\Installer\Products`, not under the `Uninstall` key you
would check for a per-machine install.
