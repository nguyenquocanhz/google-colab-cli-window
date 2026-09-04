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

## Signing

Nothing in the v0.6.0-win.1 release is signed. `dong-goi/ky-so.ps1` is the
pipeline for when a certificate exists:

```
.\dong-goi\ky-so.ps1 -Thumbprint <thumbprint of a cert in Cert:\CurrentUser\My>
```

It refuses to start without a real certificate, signs `colab.exe` and the
`.msi`, timestamps both, and verifies with `signtool verify /pa` — the
Authenticode policy, the one Windows itself applies. Verifying under the
default policy can pass for a file Windows will still refuse.

Two things worth knowing before buying anything:

- **Timestamping is not optional.** `/tr` is in the script for a reason: an
  untimestamped signature dies the day the certificate expires, including on
  copies already downloaded and installed. A timestamped one survives, because
  the timestamp proves the signature predates expiry.
- **An OV certificate may not solve the problem you bought it for.**
  SmartScreen reputation accumulates over downloads, so early users still see
  the warning. EV certificates carry reputation from the first download;
  Azure Trusted Signing is far cheaper than either but needs a verifiable legal
  entity.

There is no free certificate that Windows trusts. A self-signed one is free and
untrusted; making it trusted means asking every user to install your root
certificate, which teaches them to lower their machine's defences for you. The
honest alternative for an unsigned build is to publish SHA256 sums, tell people
plainly why the warning appears, and show them "More info → Run anyway" — which
allows one binary rather than weakening anything.
