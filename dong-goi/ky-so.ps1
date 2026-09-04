<#
.SYNOPSIS
    Sign the release binaries. Requires a code signing certificate you own.

.DESCRIPTION
    Nothing in this repository is signed. This script is the pipeline, not the
    key: it cannot conjure a certificate, and neither can anyone else. Signing
    requires a private key, and since June 2023 every publicly-trusted code
    signing certificate must keep that key on a FIPS 140-2 Level 2 hardware
    token or a cloud HSM. The old "download a .pfx and point signtool at it"
    flow no longer exists for newly issued certificates.

    Practically that leaves three routes, and the right one depends on whether
    you have a registered legal entity:

      Azure Trusted Signing   cheapest by far, signs in the cloud so the build
                              machine needs no token. Requires a verifiable
                              legal entity.
      EV certificate          SmartScreen reputation from the very first
                              download. Hardware token, shipped by post.
      OV certificate          cheaper, but SmartScreen reputation ACCUMULATES
                              over downloads, so early users still see the
                              warning. For software handed to a small known
                              audience this often does not solve the problem
                              you bought it to solve.

    There is no free option that Windows trusts. A self-signed certificate is
    free and Windows does not trust it; making it trusted means asking every
    user to install your root certificate, which teaches them to lower their
    machine's defences. Do not do that.

.PARAMETER Thumbprint
    Thumbprint of a code signing certificate in Cert:\CurrentUser\My.

.PARAMETER Files
    Files to sign. Defaults to the release artifacts.

.EXAMPLE
    .\ky-so.ps1 -Thumbprint A1B2C3...
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Thumbprint,
    [string[]]$Files = @(
        "$PSScriptRoot\..\dist-exe\colab.exe",
        "$PSScriptRoot\ColabCLI-windows-0.6.1.msi"
    ),
    # Timestamping is NOT optional. Without it every signature you make becomes
    # invalid the day the certificate expires -- including binaries already
    # downloaded and installed. With it, they stay valid because the timestamp
    # proves the signature predates expiry.
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

# --- find signtool ---------------------------------------------------------
$signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
if (-not $signtool) {
    $roots = @("C:\Program Files (x86)\Windows Kits\10\bin",
               "C:\Program Files\Windows Kits\10\bin")
    foreach ($r in $roots) {
        if (Test-Path $r) {
            $c = Get-ChildItem $r -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                 Where-Object { $_.FullName -match "x64" } |
                 Sort-Object FullName -Descending | Select-Object -First 1
            if ($c) { $signtool = $c.FullName; break }
        }
    }
}
if (-not $signtool) {
    throw @"
signtool.exe not found. It ships with the Windows SDK; the "Windows SDK Signing
Tools for Desktop Apps" component alone is enough and is a small install.
Alternatively `jsign` (Java) or `osslsigncode` can sign an MSI without the SDK.
"@
}
Write-Host "signtool : $signtool"

# --- check the certificate exists before touching any file -----------------
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -eq $Thumbprint }
if (-not $cert) {
    throw "No certificate with thumbprint $Thumbprint in Cert:\CurrentUser\My."
}
Write-Host "cert     : $($cert.Subject)"
Write-Host "expires  : $($cert.NotAfter.ToString('yyyy-MM-dd'))"
if ($cert.NotAfter -lt (Get-Date)) { throw "That certificate has expired." }

# --- sign ------------------------------------------------------------------
foreach ($f in $Files) {
    if (-not (Test-Path $f)) { Write-Warning "skipping, not found: $f"; continue }
    Write-Host "`n--- signing $f"
    & $signtool sign /sha1 $Thumbprint /fd SHA256 /td SHA256 /tr $TimestampUrl /v $f
    if ($LASTEXITCODE -ne 0) { throw "signtool sign failed on $f (exit $LASTEXITCODE)" }
}

# --- verify ----------------------------------------------------------------
# `/pa` uses the Authenticode policy, i.e. the one Windows itself applies when
# deciding whether to trust the file. Verifying with the default policy instead
# can pass for a file Windows will still refuse.
$failed = @()
foreach ($f in $Files) {
    if (-not (Test-Path $f)) { continue }
    Write-Host "`n--- verifying $f"
    & $signtool verify /pa /v $f
    if ($LASTEXITCODE -ne 0) { $failed += $f }
}
if ($failed) { throw "verification failed: $($failed -join ', ')" }

Write-Host "`nAll files signed and verified."
Write-Host "Re-run the SHA256 sums -- signing changes the bytes, so every"
Write-Host "checksum published before this point is now wrong."
