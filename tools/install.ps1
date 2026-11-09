<#
Installs (or removes) the shogun2-battle-ai loader into the game's data folders.

What it does: takes the game's own data/battle_scripted.lua out of YOUR data.pack, appends one line that runs
lua/s2ai_probe.lua from this repository, and writes the result as a loose file data\battle_scripted.lua.
Nothing in the game's packs is modified; -Uninstall deletes the loose file again.

  .\tools\install.ps1                 # install into every detected Shogun 2 Complete Edition data folder
  .\tools\install.ps1 -Uninstall
  .\tools\install.ps1 -DataDirs "X:\...\data"
#>
param(
    [string[]]$DataDirs,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path $PSScriptRoot -Parent
$RepoLua = ($Repo -replace '\\', '/')
$Marker = '-- shogun2-battle-ai loader'

if (-not $DataDirs) {
    $libs = @()
    $vdf = Get-ChildItem 'C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf', 'D:\Steam\steamapps\libraryfolders.vdf' -ErrorAction SilentlyContinue
    foreach ($v in $vdf) { $libs += (Select-String -Path $v.FullName -Pattern '"path"\s+"([^"]+)"').Matches | ForEach-Object { $_.Groups[1].Value -replace '\\\\', '\' } }
    $DataDirs = foreach ($l in ($libs | Sort-Object -Unique)) {
        foreach ($sub in 'data', 'RemasteredCE\data') {
            $d = Join-Path $l "steamapps\common\Shogun 2 Complete Edition\$sub"
            if (Test-Path (Join-Path $d 'data.pack')) { $d }
        }
    }
}
if (-not $DataDirs) { throw 'No Shogun 2 Complete Edition data folder found; pass -DataDirs.' }

foreach ($d in $DataDirs) {
    $target = Join-Path $d 'battle_scripted.lua'
    if ($Uninstall) {
        if ((Test-Path $target) -and (Select-String -Path $target -SimpleMatch $Marker -Quiet)) {
            Remove-Item $target; Write-Host "removed  $target"
        } elseif (Test-Path $target) {
            Write-Warning "left alone (not ours): $target"
        }
        continue
    }
    if ((Test-Path $target) -and -not (Select-String -Path $target -SimpleMatch $Marker -Quiet)) {
        Write-Warning "skipped: $target exists and was not written by this tool"
        continue
    }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("s2ai_" + [guid]::NewGuid())
    & (Join-Path $PSScriptRoot 'packtool.ps1') -Pack (Join-Path $d 'data.pack') -Filter '^battle_scripted\.lua$' -OutDir $tmp 6>$null | Out-Null
    $orig = Join-Path $tmp 'battle_scripted.lua'
    if (-not (Test-Path $orig)) { throw "battle_scripted.lua not found in $d\data.pack" }
    $text = [IO.File]::ReadAllText($orig).TrimEnd()
    Remove-Item $tmp -Recurse -Force
    $loader = @"


$Marker (tools/install.ps1; remove with -Uninstall)
S2AI_ROOT = "$RepoLua"
do
	local ok, err = pcall(dofile, S2AI_ROOT .. "/lua/s2ai_probe.lua")
	if not ok then
		local f = io.open(S2AI_ROOT .. "/runtime/probe.log", "a")
		if f then f:write("LOAD ERROR: ", tostring(err), "\n"); f:close() end
	end
end
"@
    [IO.File]::WriteAllText($target, ($text + $loader -replace "`r?`n", "`r`n"), (New-Object Text.UTF8Encoding($false)))
    Write-Host "installed $target"
}
if (-not $Uninstall) { New-Item -ItemType Directory -Force (Join-Path $Repo 'runtime') | Out-Null }
