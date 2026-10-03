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

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dotnetCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($env:DOTNET_ROOT)) {
  $dotnetCandidates += Join-Path $env:DOTNET_ROOT "dotnet.exe"
}
$dotnetCandidates += Join-Path (Join-Path $repoRoot ".dotnet") "dotnet.exe"
$dotnetCommand = Get-Command dotnet -ErrorAction SilentlyContinue
if ($dotnetCommand) {
  $dotnetCandidates += $dotnetCommand.Source
}

$dotnetHost = $null
$dotnetVersions = @()
$dotnetErrors = @()
foreach ($candidate in @($dotnetCandidates | Select-Object -Unique)) {
  if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
    continue
  }

  $dotnetOutput = @(& $candidate --list-sdks 2>&1)
  $dotnetExitCode = $LASTEXITCODE
  $candidateVersions = @($dotnetOutput | Where-Object { $_ -match '^\s*\d+\.\d+\.\d+' })
  if ($dotnetExitCode -eq 0 -and $candidateVersions.Count -gt 0) {
    $dotnetHost = $candidate
    $dotnetVersions = $candidateVersions
    break
  }
  if ($dotnetOutput.Count -gt 0) {
    $dotnetErrors += ("{0}: {1}" -f $candidate, ($dotnetOutput -join " "))
  }
}

$dotnetOk = $null -ne $dotnetHost
if ($dotnetOk) {
  $dotnetDetail = "$($dotnetVersions -join '; ') ($dotnetHost)"
} elseif ($dotnetErrors.Count -gt 0) {
  $dotnetDetail = "SDK unavailable: " + ($dotnetErrors -join " | ")
} else {
  $dotnetDetail = ".NET SDK not found in DOTNET_ROOT, repository .dotnet, or PATH"
}
Add-Check ".NET SDK" $dotnetOk $dotnetDetail

$odaCandidates = @(
  "$env:ProgramFilesODAODAFileConverterODAFileConverter.exe",
  "$env:ProgramFilesODAODAFileConverterODAFileConverter 26.*ODAFileConverter.exe"
)
$oda = $odaCandidates | ForEach-Object { Get-Item $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
Add-Check "ODA File Converter" ($null -ne $oda) ($(if ($oda) { $oda.FullName } else { "not found (optional)" }))

$checks | Format-Table -AutoSize
$failed = @($checks | Where-Object { -not $_.ok })
if ($Strict -and $failed.Count -gt 0) {
  throw "Doctor failed: $($failed.Count) checks failed"
}