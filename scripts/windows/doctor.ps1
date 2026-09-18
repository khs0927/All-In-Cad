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
if ($dotnet) {
  $dotnetOutput = @(& $dotnet.Source --list-sdks 2>&1)
  $dotnetExitCode = $LASTEXITCODE
  $dotnetVersions = @($dotnetOutput | Where-Object { $_ -match '^\s*\d+\.\d+\.\d+' })
  $dotnetOk = $dotnetExitCode -eq 0 -and $dotnetVersions.Count -gt 0
  if ($dotnetOk) {
    $dotnetDetail = $dotnetVersions -join "; "
  } elseif ($dotnetOutput.Count -gt 0) {
    $dotnetDetail = "SDK unavailable: " + ($dotnetOutput -join " ")
  } else {
    $dotnetDetail = "no .NET SDKs installed"
  }
} else {
  $dotnetOk = $false
  $dotnetDetail = "dotnet command not found"
}
Add-Check ".NET SDK" $dotnetOk $dotnetDetail

$odaCandidates = @(
  "$env:ProgramFiles\ODA\ODAFileConverter\ODAFileConverter.exe",
  "$env:ProgramFiles\ODA\ODAFileConverter\ODAFileConverter 26.*\ODAFileConverter.exe"
)
$oda = $odaCandidates | ForEach-Object { Get-Item $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
Add-Check "ODA File Converter" ($null -ne $oda) ($(if ($oda) { $oda.FullName } else { "not found (optional)" }))

$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { -not $_.ok })
if ($Strict -and $failed.Count -gt 0) {
  throw "Doctor failed: $($failed.Count) checks failed"
}
