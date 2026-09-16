param(
  [string]$Output = ".\artifacts\host-benchmark.json"
)

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $Output
if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }

$rows = @()
foreach ($name in @("acad", "zwcad")) {
  $processes = @(Get-Process $name -ErrorAction SilentlyContinue)
  foreach ($process in $processes) {
    $rows += [pscustomobject]@{
      host = $name
      pid = $process.Id
      started = $process.StartTime.ToString("o")
      responding = $process.Responding
      mainWindowHandle = [string]$process.MainWindowHandle
    }
  }
}

$result = [pscustomobject]@{
  capturedAt = (Get-Date).ToString("o")
  hosts = $rows
  next = @(
    "Run Autodesk Official MCP drawing query smoke test",
    "Run bimwright/dwg-mcp target discovery",
    "Run PyRx common-capability script in AutoCAD 2027",
    "Run PyRx common-capability script in ZWCAD 2026"
  )
}
$result | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $Output
Write-Host "Wrote $Output"
