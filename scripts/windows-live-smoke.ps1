<#
.SYNOPSIS
  Repeatable AutoCAD 2027 live-host smoke for All-In-Cad. Writes JSON evidence under artifacts/live-smoke/.

.DESCRIPTION
  Three modes, from least to most invasive:

    -DryRun   detection only. Never connects to CAD. Reports the OS, toolchain (doctor.ps1),
              AutoCAD 2027 install, plugin build, running acad.exe and worker descriptors.
    (default) read-only. Additionally talks to the running All-In-Cad worker over its
              current-user Named Pipe: system.ping, host.capabilities, host.context.
              Nothing is written to any drawing.
    -Write    additionally creates a NEW drawing in %TEMP% through COM, refuses to continue
              unless the worker reports that temp drawing as its active document, draws one
              LINE through the pipe (host.draw_line), reads it back independently through COM,
              then closes the temp drawing and restores the previously active document.
              Drawings the user has open are never modified, saved or closed.

  Every run gets its own directory artifacts/live-smoke/<runId>/ containing live-smoke.json
  (schema all-in-cad/live-smoke/v1) plus the raw responses it was built from, each with a
  sha256. Feed it to `all-in-cad live-smoke ingest` to update docs/verification-ledger.json and,
  with --apply, tick the ROADMAP items the run proved.

  The worker pipe is PipeOptions.CurrentUserOnly and COM attaches through the per-session
  Running Object Table, so this must run as the same interactive user that runs AutoCAD
  (a GitHub runner installed as a Windows service will not see the session).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\windows-live-smoke.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File scripts\windows-live-smoke.ps1
  powershell -ExecutionPolicy Bypass -File scripts\windows-live-smoke.ps1 -Write
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$Write,
  [string]$OutputDir = "",
  [int]$ProcessId = 0,
  [int]$TimeoutMs = 15000,
  [switch]$KeepTemp
)

$ErrorActionPreference = "Stop"

if ($DryRun -and $Write) {
  throw "-DryRun and -Write are mutually exclusive."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDir) {
  $OutputDir = Join-Path $repoRoot "artifacts\live-smoke"
}
$mode = "read-only"
if ($DryRun) { $mode = "dry-run" }
if ($Write) { $mode = "write" }

$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
$runDir = Join-Path $OutputDir $runId
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$script:steps = New-Object System.Collections.ArrayList
$script:stepOutputs = New-Object System.Collections.ArrayList
$script:versions = [ordered]@{}
$script:state = @{}

function Get-Sha256([string]$Path) {
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Save-Output([string]$Name, $Value) {
  # Persist a raw step output next to the evidence and record its digest.
  $path = Join-Path $runDir $Name
  if ($Value -is [string]) {
    [System.IO.File]::WriteAllText($path, $Value, (New-Object System.Text.UTF8Encoding($false)))
  } else {
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
  }
  [void]$script:stepOutputs.Add([ordered]@{ path = $Name; sha256 = (Get-Sha256 $path) })
  return $path
}

function Invoke-Step([string]$Id, [scriptblock]$Body) {
  # A body returns a detail hashtable. It may set detail._status = "skipped" to skip, and
  # it fails the step by throwing. Steps never stop the run; dependants check $script:state.
  $script:stepOutputs.Clear()
  $started = Get-Date
  $status = "passed"
  $detail = [ordered]@{}
  try {
    $result = & $Body
    if ($null -ne $result) { $detail = $result }
    if ($detail.Contains("_status")) {
      $status = [string]$detail["_status"]
      $detail.Remove("_status")
    }
  } catch {
    $status = "failed"
    $detail["error"] = $_.Exception.Message
  }
  $outputs = @($script:stepOutputs | ForEach-Object { $_ })
  [void]$script:steps.Add([ordered]@{
    id = $Id
    status = $status
    started_at = $started.ToUniversalTime().ToString("o")
    duration_ms = [int]((Get-Date) - $started).TotalMilliseconds
    detail = $detail
    outputs = $outputs
  })
  Write-Host ("[{0,-7}] {1}" -f $status, $Id)
  return ($status -eq "passed")
}

function Skip([string]$Reason) {
  return [ordered]@{ _status = "skipped"; reason = $Reason }
}

function Read-Exact($Stream, [int]$Count) {
  $buffer = New-Object byte[] $Count
  $offset = 0
  while ($offset -lt $Count) {
    $task = $Stream.ReadAsync($buffer, $offset, $Count - $offset)
    if (-not $task.Wait($TimeoutMs)) { throw "pipe read timed out after $TimeoutMs ms" }
    if ($task.Result -le 0) { throw "pipe closed before a full frame arrived" }
    $offset += $task.Result
  }
  return ,$buffer
}

function Invoke-AicRpc([string]$PipeName, [string]$Method, [hashtable]$Params) {
  # aic.native/1: 4-byte little-endian length prefix + UTF-8 JSON, 8 MiB limit (native/shared/FrameCodec.cs).
  if ($null -eq $Params) { $Params = @{} }
  $client = New-Object System.IO.Pipes.NamedPipeClientStream(".", $PipeName, [System.IO.Pipes.PipeDirection]::InOut, [System.IO.Pipes.PipeOptions]::Asynchronous)
  try {
    $client.Connect($TimeoutMs)
    $request = [ordered]@{
      protocol = "aic.native/1"
      request_id = [guid]::NewGuid().ToString()
      method = $Method
      params = $Params
    }
    if ($env:AIC_AUTOCAD_SESSION_TOKEN) { $request["session_token"] = $env:AIC_AUTOCAD_SESSION_TOKEN }
    $body = [System.Text.Encoding]::UTF8.GetBytes(($request | ConvertTo-Json -Depth 10 -Compress))
    $header = [System.BitConverter]::GetBytes([int32]$body.Length)
    $client.Write($header, 0, 4)
    $client.Write($body, 0, $body.Length)
    $client.Flush()
    $length = [System.BitConverter]::ToInt32((Read-Exact $client 4), 0)
    if ($length -le 0 -or $length -gt 8MB) { throw "invalid response frame length $length" }
    $text = [System.Text.Encoding]::UTF8.GetString((Read-Exact $client $length))
    return [pscustomobject]@{ raw = $text; response = ($text | ConvertFrom-Json) }
  } finally {
    $client.Dispose()
  }
}

function Assert-RpcOk($Reply, [string]$Method) {
  if (-not $Reply.response.ok) {
    $code = ""
    if ($Reply.response.error) { $code = "$($Reply.response.error.code): $($Reply.response.error.message)" }
    throw "$Method failed: $code"
  }
  return $Reply.response.result
}

function Get-Prop($Object, [string[]]$Names) {
  # System.Text.Json keeps PascalCase for named types and snake_case for anonymous ones.
  foreach ($name in $Names) {
    if ($null -ne $Object -and $Object.PSObject.Properties[$name]) { return $Object.$name }
  }
  return $null
}

function Normalize-Path([string]$Path) {
  if (-not $Path) { return "" }
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\').ToLowerInvariant()
}

# ---------------------------------------------------------------- detection (all modes)

Invoke-Step "env.detect" {
  $ErrorActionPreference = "Continue"  # Windows PowerShell 5.1: native stderr must not abort the step
  $commit = $null
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) { $commit = (& git -C $repoRoot rev-parse HEAD 2>$null) }
  $script:versions["powershell"] = $PSVersionTable.PSVersion.ToString()
  $script:versions["os"] = [System.Environment]::OSVersion.VersionString
  $script:state["commit"] = $commit
  [ordered]@{
    computer = $env:COMPUTERNAME
    user = $env:USERNAME
    user_interactive = [System.Environment]::UserInteractive
    session_id = (Get-Process -Id $PID).SessionId
    is_64bit = [System.Environment]::Is64BitProcess
    git_commit = $commit
    temp = $env:TEMP
  }
} | Out-Null

Invoke-Step "doctor" {
  $doctorJson = Join-Path $runDir "doctor.json"
  & (Join-Path $PSScriptRoot "windows\doctor.ps1") -Json $doctorJson | Out-Null
  [void]$script:stepOutputs.Add([ordered]@{ path = "doctor.json"; sha256 = (Get-Sha256 $doctorJson) })
  $checks = Get-Content -Raw -LiteralPath $doctorJson | ConvertFrom-Json
  foreach ($check in $checks) {
    if ($check.name -eq ".NET SDK" -and $check.ok) { $script:versions["dotnet_sdk"] = [string]$check.detail }
    if ($check.name -eq "Python" -and $check.ok) { $script:versions["python"] = [string]$check.detail }
  }
  [ordered]@{ checks = @($checks) }
} | Out-Null

Invoke-Step "autocad.install" {
  $locations = @()
  foreach ($key in @("HKLM:\SOFTWARE\Autodesk\AutoCAD\R26.0")) {
    if (Test-Path $key) {
      Get-ChildItem $key -ErrorAction SilentlyContinue | ForEach-Object {
        $location = (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).AcadLocation
        if ($location) { $locations += $location }
      }
    }
  }
  $locations += Join-Path $env:ProgramFiles "Autodesk\AutoCAD 2027"
  $exe = $locations | ForEach-Object { Join-Path $_ "acad.exe" } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (-not $exe) { throw "AutoCAD 2027 (acad.exe, registry R26.0) not found" }
  $info = (Get-Item -LiteralPath $exe).VersionInfo
  $script:versions["autocad"] = $info.ProductVersion
  $script:state["acadDir"] = Split-Path -Parent $exe
  [ordered]@{ exe = $exe; product_version = $info.ProductVersion; file_version = $info.FileVersion }
} | Out-Null

Invoke-Step "plugin.build" {
  $dll = Get-ChildItem -Path (Join-Path $repoRoot "native\autocad2027\bin") -Filter "AllInCad.AutoCAD2027.dll" -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $dll) {
    return (Skip "native/autocad2027 not built (dotnet build native/autocad2027/AllInCad.AutoCAD2027.csproj -c Release -p:ACAD2027_DIR=...)")
  }
  $script:versions["plugin_file_version"] = $dll.VersionInfo.FileVersion
  [ordered]@{ path = $dll.FullName; file_version = $dll.VersionInfo.FileVersion; sha256 = (Get-Sha256 $dll.FullName); built_at = $dll.LastWriteTimeUtc.ToString("o") }
} | Out-Null

Invoke-Step "autocad.process" {
  $processes = @(Get-Process acad -ErrorAction SilentlyContinue)
  if ($ProcessId -gt 0) { $processes = @($processes | Where-Object { $_.Id -eq $ProcessId }) }
  if ($processes.Count -eq 0) {
    if ($DryRun) { return (Skip "acad.exe is not running") }
    throw "acad.exe is not running (start AutoCAD 2027 and NETLOAD the plugin first)"
  }
  [ordered]@{ pids = @($processes | ForEach-Object { $_.Id }); session_ids = @($processes | ForEach-Object { $_.SessionId }) }
} | Out-Null

Invoke-Step "worker.descriptor" {
  $dir = Join-Path $env:LOCALAPPDATA "All-In-Cad\workers"
  $found = @()
  if (Test-Path -LiteralPath $dir) {
    foreach ($file in Get-ChildItem -LiteralPath $dir -Filter "autocad2027-*.json" -ErrorAction SilentlyContinue) {
      $descriptor = Get-Content -Raw -LiteralPath $file.FullName | ConvertFrom-Json
      $alive = $null -ne (Get-Process -Id $descriptor.process_id -ErrorAction SilentlyContinue)
      $found += [ordered]@{ file = $file.Name; pipe_name = $descriptor.pipe_name; process_id = $descriptor.process_id; alive = $alive; started_at = $descriptor.started_at }
    }
  }
  $live = @($found | Where-Object { $_.alive -and ($ProcessId -le 0 -or $_.process_id -eq $ProcessId) })
  if ($live.Count -gt 0) {
    $script:state["pipe"] = $live[0].pipe_name
  } elseif ($ProcessId -gt 0) {
    $script:state["pipe"] = "all-in-cad-acad-$ProcessId"
  } else {
    $acad = Get-Process acad -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($acad) { $script:state["pipe"] = "all-in-cad-acad-$($acad.Id)" }
  }
  if (-not $script:state["pipe"]) {
    if ($DryRun) { return (Skip "no live worker descriptor and no acad.exe") }
    throw "no live worker: NETLOAD native/autocad2027 in AutoCAD 2027 first"
  }
  [ordered]@{ descriptors = $found; selected_pipe = $script:state["pipe"] }
} | Out-Null

# ---------------------------------------------------------------- read-only pipe checks

if (-not $DryRun) {
  Invoke-Step "pipe.ping" {
    if (-not $script:state["pipe"]) { throw "no pipe selected" }
    $reply = Invoke-AicRpc $script:state["pipe"] "system.ping" @{}
    Save-Output "rpc-system.ping.json" $reply.raw | Out-Null
    $result = Assert-RpcOk $reply "system.ping"
    if (-not $result.pong) { throw "system.ping did not return pong" }
    if ($result.host -ne "autocad2027") { throw "unexpected host '$($result.host)'" }
    $script:versions["plugin_version"] = [string]$result.plugin_version
    $script:state["pingOk"] = $true
    [ordered]@{ host = $result.host; process_id = $result.process_id; sdk_bound = $result.sdk_bound; plugin_version = $result.plugin_version; protocol = $result.protocol }
  } | Out-Null

  Invoke-Step "pipe.capabilities" {
    if (-not $script:state["pingOk"]) { throw "system.ping did not pass" }
    $reply = Invoke-AicRpc $script:state["pipe"] "host.capabilities" @{}
    Save-Output "rpc-host.capabilities.json" $reply.raw | Out-Null
    $result = Assert-RpcOk $reply "host.capabilities"
    if (-not $result.sdk_bound) { throw "worker was built without AIC_AUTOCAD_SDK" }
    [ordered]@{ capabilities = @($result.capabilities); sdk_bound = $result.sdk_bound }
  } | Out-Null

  Invoke-Step "pipe.context" {
    if (-not $script:state["pingOk"]) { throw "system.ping did not pass" }
    $reply = Invoke-AicRpc $script:state["pipe"] "host.context" @{}
    Save-Output "rpc-host.context.json" $reply.raw | Out-Null
    $result = Assert-RpcOk $reply "host.context"
    if ($result.host_version) { $script:versions["autocad_runtime"] = [string]$result.host_version }
    $script:state["contextOk"] = $true
    # The user's document path is recorded as a digest only: evidence must not leak drawing names.
    $pathDigest = $null
    if ($result.document.path) {
      $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$result.document.path)
      $pathDigest = ([System.BitConverter]::ToString((New-Object System.Security.Cryptography.SHA256Managed).ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    [ordered]@{ host_version = $result.host_version; sdk_bound = $result.sdk_bound; document_active = $result.document.is_active; entity_count = $result.document.entity_count; revision = $result.document.revision; document_path_sha256 = $pathDigest }
  } | Out-Null
}

# ---------------------------------------------------------------- guarded write on a temp drawing

if ($Write) {
  $tempRoot = Join-Path $env:TEMP "all-in-cad-live-smoke\$runId"
  $tempDwg = Join-Path $tempRoot "live-smoke.dwg"
  $acadApp = $null
  $tempDoc = $null
  $previousDoc = $null
  try {
    Invoke-Step "com.attach" {
      if (-not $script:state["contextOk"]) { throw "read-only pipe checks did not pass" }
      if ($PSVersionTable.PSEdition -eq "Core") { throw "COM attach needs Windows PowerShell 5.1 (powershell.exe), not pwsh" }
      $script:acadApp = [System.Runtime.InteropServices.Marshal]::GetActiveObject("AutoCAD.Application.26")
      $version = [string]$script:acadApp.Version
      if (-not $version.StartsWith("26.")) { throw "COM attached to AutoCAD $version, expected 26.x (2027)" }
      [ordered]@{ com_version = $version; documents_open = $script:acadApp.Documents.Count }
    } | Out-Null

    Invoke-Step "temp.create" {
      if (-not $script:acadApp) { throw "COM attach did not pass" }
      New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
      if ($script:acadApp.Documents.Count -gt 0) { $script:previousDoc = $script:acadApp.ActiveDocument }
      $script:tempDoc = $script:acadApp.Documents.Add()
      $script:acadApp.ActiveDocument = $script:tempDoc
      $script:tempDoc.SaveAs($tempDwg)
      [ordered]@{ temp_drawing = $tempDwg; clayer = [string]$script:tempDoc.GetVariable("CLAYER"); modelspace_count = $script:tempDoc.ModelSpace.Count }
    } | Out-Null

    Invoke-Step "guard.active_is_temp" {
      if (-not $script:tempDoc) { throw "temp drawing was not created" }
      # The worker writes to MdiActiveDocument. Refuse to draw unless that is provably our temp drawing.
      $reply = Invoke-AicRpc $script:state["pipe"] "host.context" @{}
      Save-Output "rpc-host.context.temp.json" $reply.raw | Out-Null
      $result = Assert-RpcOk $reply "host.context"
      if ((Normalize-Path ([string]$result.document.path)) -ne (Normalize-Path $tempDwg)) {
        throw "worker active document is not the temp drawing; refusing to write"
      }
      if ([int]$result.document.entity_count -ne 0) { throw "temp drawing is not empty" }
      $script:state["guardOk"] = $true
      [ordered]@{ document_id = $result.document.document_id; revision = $result.document.revision; entity_count = $result.document.entity_count }
    } | Out-Null

    $requested = [ordered]@{ x1 = 0.0; y1 = 0.0; x2 = 1234.5; y2 = 678.9 }
    Invoke-Step "pipe.draw_line" {
      if (-not $script:state["guardOk"]) { throw "temp-drawing guard did not pass" }
      $reply = Invoke-AicRpc $script:state["pipe"] "host.draw_line" @{ x1 = $requested.x1; y1 = $requested.y1; x2 = $requested.x2; y2 = $requested.y2 }
      Save-Output "rpc-host.draw_line.json" $reply.raw | Out-Null
      $result = Assert-RpcOk $reply "host.draw_line"
      $after = Get-Prop $result.after @("EntityCount", "entity_count")
      if ([int]$result.delta_entities -ne 1) { throw "expected exactly one new entity, got $($result.delta_entities)" }
      if (-not $result.line_id) { throw "no handle returned" }
      $script:state["lineHandle"] = [string]$result.line_id
      $script:state["pipeAfterCount"] = [int]$after
      [ordered]@{ handle = $result.line_id; delta_entities = $result.delta_entities; entity_count_after = $after; revision_after = (Get-Prop $result.after @("Revision", "revision")) }
    } | Out-Null

    Invoke-Step "com.readback" {
      if (-not $script:state["lineHandle"]) { throw "no LINE handle to read back" }
      $entity = $script:tempDoc.HandleToObject($script:state["lineHandle"])
      $start = @($entity.StartPoint)
      $end = @($entity.EndPoint)
      $layer = [string]$entity.Layer
      $clayer = [string]$script:tempDoc.GetVariable("CLAYER")
      $count = [int]$script:tempDoc.ModelSpace.Count
      $detail = [ordered]@{
        handle = $script:state["lineHandle"]
        object_name = [string]$entity.ObjectName
        com_modelspace_count = $count
        pipe_entity_count_after = $script:state["pipeAfterCount"]
        requested = [ordered]@{ entity_type = "LINE"; layer = $clayer; start = @($requested.x1, $requested.y1, 0.0); end = @($requested.x2, $requested.y2, 0.0) }
        actual = [ordered]@{ entity_type = "LINE"; layer = $layer; start = @([double]$start[0], [double]$start[1], [double]$start[2]); end = @([double]$end[0], [double]$end[1], [double]$end[2]) }
      }
      if ($detail.object_name -ne "AcDbLine") { throw "readback is $($detail.object_name), not AcDbLine" }
      if ($count -ne $script:state["pipeAfterCount"]) { throw "COM model space count $count != pipe count $($script:state['pipeAfterCount'])" }
      $tolerance = 1e-6
      for ($i = 0; $i -lt 3; $i++) {
        if ([math]::Abs($detail.requested.start[$i] - $detail.actual.start[$i]) -gt $tolerance) { throw "start point differs at axis $i" }
        if ([math]::Abs($detail.requested.end[$i] - $detail.actual.end[$i]) -gt $tolerance) { throw "end point differs at axis $i" }
      }
      if ($layer -ne $clayer) { throw "layer '$layer' != CLAYER '$clayer'" }
      $detail
    } | Out-Null

    Invoke-Step "temp.save_digest" {
      if (-not $script:tempDoc) { return (Skip "no temp drawing") }
      $script:tempDoc.Save()
      $copy = Join-Path $runDir "live-smoke.dwg"
      Copy-Item -LiteralPath $tempDwg -Destination $copy -Force
      [void]$script:stepOutputs.Add([ordered]@{ path = "live-smoke.dwg"; sha256 = (Get-Sha256 $copy) })
      [ordered]@{ bytes = (Get-Item -LiteralPath $copy).Length }
    } | Out-Null
  } finally {
    Invoke-Step "temp.cleanup" {
      $closed = $false
      if ($script:tempDoc) {
        $script:tempDoc.Close($false)
        $closed = $true
      }
      if ($script:previousDoc) {
        try { $script:acadApp.ActiveDocument = $script:previousDoc } catch { }
      }
      if (-not $KeepTemp -and (Test-Path -LiteralPath $tempRoot)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
      }
      [ordered]@{ closed_temp_drawing = $closed; kept = [bool]$KeepTemp; temp_root = $tempRoot }
    } | Out-Null
  }
}

# ---------------------------------------------------------------- evidence

$failed = @($script:steps | Where-Object { $_.status -eq "failed" }).Count
$passed = @($script:steps | Where-Object { $_.status -eq "passed" }).Count
$skipped = @($script:steps | Where-Object { $_.status -eq "skipped" }).Count
$evidence = [ordered]@{
  schema = "all-in-cad/live-smoke/v1"
  run_id = $runId
  captured_at = (Get-Date).ToUniversalTime().ToString("o")
  mode = $mode
  host = "autocad2027"
  machine = [ordered]@{ computer = $env:COMPUTERNAME; runner = $env:RUNNER_NAME; workflow_run = $env:GITHUB_RUN_ID }
  git_commit = $script:state["commit"]
  versions = $script:versions
  steps = @($script:steps)
  summary = [ordered]@{ passed = $passed; failed = $failed; skipped = $skipped; ok = ($failed -eq 0) }
}
$evidencePath = Join-Path $runDir "live-smoke.json"
[System.IO.File]::WriteAllText($evidencePath, ($evidence | ConvertTo-Json -Depth 14), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Evidence: $evidencePath (sha256 $(Get-Sha256 $evidencePath))"
Write-Host "Summary: passed=$passed failed=$failed skipped=$skipped mode=$mode"
if ($failed -gt 0) { exit 1 }
exit 0
