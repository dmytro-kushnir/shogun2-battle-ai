# Builds native/s2ai_native.dll (and the offline test) with Zig (winget install zig.zig), then runs the offline
# test against the engine DLL: it maps the engine without running it and checks the patch.
#   .\native\build.ps1 [-Engine <path to CE.dll or empire.retail.x64.dll>]
param([string]$Engine = 'F:\SteamLibrary\steamapps\common\Shogun 2 Complete Edition\RemasteredCE\CE.dll')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    zig cc -target x86_64-windows-gnu -O2 -shared -Wall -Wextra -o s2ai_native.dll s2ai_native.c
    if ($LASTEXITCODE) { throw 'build of s2ai_native.dll failed' }
    zig cc -target x86_64-windows-gnu -O1 -o test_patch.exe test_patch.c
    if ($LASTEXITCODE) { throw 'build of test_patch.exe failed' }
    Remove-Item *.pdb, *.lib -ErrorAction SilentlyContinue
    if (Test-Path $Engine) { .\test_patch.exe $Engine }
} finally { Pop-Location }
