# Builds native/s2ai_native.dll (and the offline test) with Zig (winget install zig.zig), then runs the offline
# test against each engine DLL found through Steam: it maps the engine WITHOUT running it and checks the patch.
#   .\native\build.ps1 [-Engine <path to CE.dll or empire.retail.x64.dll>]
param([string]$Engine)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    zig cc -target x86_64-windows-gnu -O2 -shared -Wall -Wextra -o s2ai_native.dll s2ai_native.c
    if ($LASTEXITCODE) { throw 'build of s2ai_native.dll failed' }
    zig cc -target x86_64-windows-gnu -O1 -o test_patch.exe test_patch.c
    if ($LASTEXITCODE) { throw 'build of test_patch.exe failed' }
    Remove-Item *.pdb, *.lib -ErrorAction SilentlyContinue
    $engines = if ($Engine) { @($Engine) } else {
        & (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe') -c "import sys; sys.path.insert(0, '..'); from s2ai.gamepaths import engine_dlls; [print(p) for p in engine_dlls()]"
    }
    foreach ($e in $engines) {
        Write-Host "--- offline patch test: $e"
        .\test_patch.exe $e
        if ($LASTEXITCODE) { throw "offline patch test failed for $e" }
    }
} finally { Pop-Location }
