# ZWCAD 2026 native adapter

The adapter binds only the managed assemblies shipped with the installed ZWCAD 2026:
`ZwManaged.dll` and `ZwDatabaseMgd.dll` (assembly version 26.0.26.0). The .NET Framework 4.8
worker exposes current-user-only Named Pipe RPC for read-only `system.ping`, `host.context`, and
`host.capabilities`. It advertises no write methods, no revision tracking, and rejects
`plan.execute` with `E_METHOD_DISABLED`. Database context reads are dispatched onto ZWCAD's UI
thread. The pipe uses an explicit ACL for the current Windows user because the adapter targets
.NET Framework 4.8.

Build on a machine with ZWCAD 2026 installed:

```powershell
$env:DOTNET_ROOT = Join-Path (Get-Location) '.dotnet'
$env:DOTNET_CLI_HOME = Join-Path (Get-Location) '.dotnet-home'
$env:ZWCAD2026_DIR = 'E:\Program files\ZWCAD 2026'
& "$env:DOTNET_ROOT\dotnet.exe" build native\zwcad2026\AllInCad.ZWCAD2026.csproj --configuration Release
```

Load `native\zwcad2026\bin\Release\net48\AllInCad.ZWCAD2026.dll` through ZWCAD's
`NETLOAD` command, then run `AIC_STATUS`. Keep secure loading enabled and trust the output
folder through ZWCAD's trusted-location settings if required.

With the plugin loaded, verify the pipe and read-only method contract using the ZWCAD process ID:

```powershell
$zwcad = Get-Process ZWCAD | Where-Object { $_.Path -like '*\ZWCAD 2026\ZWCAD.exe' } | Select-Object -First 1
if (-not $zwcad) { throw 'ZWCAD 2026 is not running.' }
& "$env:DOTNET_ROOT\dotnet.exe" run --project native\smoke\AllInCad.SmokeClient.csproj -- $zwcad.Id zwcad
```

The pipe name is `all-in-cad-zwcad-<process id>`. If `AIC_ZWCAD_SESSION_TOKEN` is set before
ZWCAD starts, the smoke client reads the same environment variable for authentication. A successful
local build does not replace live loading and smoke evidence.

The ZRX.NET choice is intentionally separate from AutoCAD's ObjectARX API. PyRx/ZRX discovery and
live native transactions remain separate acceptance gates.
