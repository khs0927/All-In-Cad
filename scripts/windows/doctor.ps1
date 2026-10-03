param(
  [switch]$Strict
)

$ErrorActionPreference = "Stop"
$checks = @()

function Add-Check($Name, $Ok, $Detail) {
  $script:checks += [pscustomobject]@{ name = $Name; ok = [bool]$Ok; detail = $Detail }
}

$acad = Get-Process acad -ErrorAction SilentlyContinue | Select-Object -First 1
$zwcad = Get-Process zwcad -ErrorAction SilentlyContinue | Select-Object -First 1
Add-Check "AutoCAD running" ($null -ne $acad) ($(if ($acad) { "PID $($acad.Id)" } else { "not running" }))
Add-Check "ZWCAD running" ($null -ne $zwcad) ($(if ($zwcad) { "PID $($zwcad.Id)" } else { "not running" }))

$python = Get-Command python -ErrorAction SilentlyContinue
Add-Check "Python" ($null -ne $python) ($(if ($python) { & python --version 2>&1 } else { "not found" }))

$dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
$dotnetDetail = if ($dotnet) { & dotnet --version 2>&1 } else { "not found" }
Add-Check ".NET SDK" ($null -ne $dotnet) $dotnetDetail

# ODA installs into a version-stamped directory, so no version literal may appear
# here: any "ODAFileConverter*" directory under an ODA install root matches, newest first.
$odaExeName = "ODAFileConverter.exe"
$odaRoots = @(
  $env:ProgramFiles, $env:ProgramW6432, ${env:ProgramFiles(x86)}
) | Where-Object { $_ } | ForEach-Object { Join-Path $_ "ODA" } | Where-Object { Test-Path $_ }
$oda = @(
  Get-ChildItem -Path $odaRoots -Filter "ODAFileConverter*" -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Get-Item (Join-Path $_.FullName $odaExeName) -ErrorAction SilentlyContinue }
  Get-Item (Join-Path $env:ProgramFiles "ODA\ODAFileConverter\$odaExeName") -ErrorAction SilentlyContinue
  Get-Command $odaExeName -ErrorAction SilentlyContinue | ForEach-Object { Get-Item $_.Source -ErrorAction SilentlyContinue }
) | Sort-Object FullName -Descending | Select-Object -First 1
Add-Check "ODA File Converter" ($null -ne $oda) ($(if ($oda) { $oda.FullName } else { "not found (optional)" }))

$acadsharpProbe = Get-ChildItem -Path (Join-Path $PSScriptRoot "..\..\native\headless\bin") -Filter "AllInCad.ACadSharpProbe.exe" -Recurse -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
Add-Check "ACadSharp DWG probe" ($null -ne $acadsharpProbe) ($(if ($acadsharpProbe) { $acadsharpProbe.FullName } else { "not built (optional)" }))
$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { -not $_.ok })
if ($Strict -and $failed.Count -gt 0) {
  throw "Doctor failed: $($failed.Count) checks failed"
}
