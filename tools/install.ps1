<#
Installs (or removes) shogun2-battle-ai's game-side hooks.

Nothing in the game's own packs is modified. For each game data folder (Shogun 2 Complete Edition's, and
Remastered CE's if installed) it writes:
  s2ai_battle.pack  a small "movie" pack (the engine loads those by itself) holding:
    frontend_scripted.lua                 the game's main-menu script + a loader for lua/s2ai_frontend.lua,
                                          which loads native/s2ai_native.dll (engine patch for script-less battles)
    s2ai\custom.battle_script             loader for lua/s2ai_bridge.lua: the script of custom and campaign battles
    shogun_historical_battles\...         the five menu historical battles' scripts with the bridge loader in front
  plus the same files as loose copies (handy to inspect; the pack is what the game uses).
The game's own scripts are taken from YOUR data.pack at install time; none are shipped with this repository.

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
# The game's Lua opens files and DLLs with ANSI paths: a folder name with non-ASCII letters (e.g. a Cyrillic user
# name) would make every hook fail silently.
if ($Repo -match '[^\x20-\x7E]') {
    throw "The repository path '$Repo' contains non-ASCII characters, which the game cannot open. Move it to a plain ASCII path such as C:\Games\shogun2-battle-ai and run install.ps1 again."
}
$RepoLua = ($Repo -replace '\\', '/')
$Marker = '-- shogun2-battle-ai loader'
$PackName = 's2ai_battle.pack'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'

$Targets = @(
    @{ Path = 'frontend_scripted.lua'; Mode = 'append'; Script = 's2ai_frontend.lua' }
    @{ Path = 's2ai\custom.battle_script'; Mode = 'new'; Script = 's2ai_bridge.lua' }
) + (@('kawanakajima\kawanakajima_battle', 'nagashino\nagashino_battle', 'okehazama\okehazama_battle',
        'sekigahara\sekigahara_battle', 'nagashima\nagashima_battle') | ForEach-Object {
        @{ Path = "shogun_historical_battles\$_.battle_script"; Mode = 'prepend'; Script = 's2ai_bridge.lua' }
    })

function Find-DataDirs {
    $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
    if (-not $steam) { return @() }
    $libs = @($steam)
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (Test-Path $vdf) {
        $libs += (Select-String -Path $vdf -Pattern '"path"\s+"([^"]+)"').Matches | ForEach-Object { $_.Groups[1].Value -replace '\\\\', '\' }
    }
    foreach ($l in ($libs | Sort-Object -Unique)) {
        foreach ($sub in 'data', 'RemasteredCE\data') {
            $d = Join-Path $l "steamapps\common\Shogun 2 Complete Edition\$sub"
            if (Test-Path (Join-Path $d 'data.pack')) { $d }
        }
    }
}

# Every loader dofile()s a script from this repository inside pcall: an error in our code must never stop the
# game's own script (the historical battle loaders sit at the top of those scripts).
function Get-Loader($script) {
    @"
$Marker (tools/install.ps1; remove with -Uninstall)
S2AI_ROOT = "$RepoLua"
do
	local ok, err = pcall(dofile, S2AI_ROOT .. "/lua/$script")
	if not ok then
		pcall(function()
			local f = io.open(S2AI_ROOT .. "/runtime/loader.log", "a")
			f:write(os.date("%Y-%m-%d %H:%M:%S"), " ${script}: ", tostring(err), "\n"); f:close()
		end)
	end
end
-- end of shogun2-battle-ai loader
"@
}

function Remove-Ours($d, $rel) {
    $target = Join-Path $d $rel
    if ((Test-Path $target) -and (Select-String -Path $target -SimpleMatch $Marker -Quiet)) {
        Remove-Item $target; Write-Host "removed  $target"
        $dir = Split-Path $target
        while ($dir -ne $d -and (Test-Path $dir) -and -not (Get-ChildItem $dir -Force)) { Remove-Item $dir; $dir = Split-Path $dir }
    } elseif (Test-Path $target) {
        Write-Warning "left alone (not ours): $target"
    }
}

if (-not $DataDirs) { $DataDirs = @(Find-DataDirs) }
if (-not $DataDirs) { throw 'No Shogun 2 Complete Edition data folder found through Steam; pass -DataDirs.' }

foreach ($d in $DataDirs) {
    if ($Uninstall) {
        $pack = Join-Path $d $PackName
        if (Test-Path $pack) { Remove-Item $pack; Write-Host "removed  $pack" }
        foreach ($t in $Targets) { Remove-Ours $d $t.Path }
        continue
    }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("s2ai_" + [guid]::NewGuid())
    $filter = '^(' + (($Targets | Where-Object { $_.Mode -ne 'new' } | ForEach-Object { [regex]::Escape($_.Path) }) -join '|') + ')$'
    & (Join-Path $PSScriptRoot 'packtool.ps1') -Pack (Join-Path $d 'data.pack') -Filter $filter -OutDir $tmp 6>$null | Out-Null
    $packArgs = @()
    foreach ($t in $Targets) {
        $target = Join-Path $d $t.Path
        if ((Test-Path $target) -and -not (Select-String -Path $target -SimpleMatch $Marker -Quiet)) {
            Write-Warning "skipped: $target exists and was not written by this tool"
            continue
        }
        $loader = Get-Loader $t.Script
        if ($t.Mode -eq 'new') {
            $out = $loader
        } else {
            $orig = Join-Path $tmp $t.Path
            if (-not (Test-Path $orig)) { Write-Warning "$($t.Path) not found in $d\data.pack"; continue }
            $text = [IO.File]::ReadAllText($orig).TrimEnd()
            $out = if ($t.Mode -eq 'append') { $text + "`n`n" + $loader } else { $loader + "`n`n" + $text }
        }
        New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
        [IO.File]::WriteAllText($target, ($out -replace "`r?`n", "`r`n") + "`r`n", (New-Object Text.UTF8Encoding($false)))
        $packArgs += "$($t.Path)=$target"
    }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    & $Python (Join-Path $PSScriptRoot 's2pack.py') (Join-Path $d $PackName) @packArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "s2pack.py failed for $d" }
    Write-Host "installed $(Join-Path $d $PackName) ($($packArgs.Count) scripts)"
}
if (-not $Uninstall) { New-Item -ItemType Directory -Force (Join-Path $Repo 'runtime') | Out-Null }
