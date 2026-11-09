<#
Installs (or removes) the shogun2-battle-ai loader into the game's data folders.

For each target script it takes the game's own copy out of YOUR data.pack, adds a loader that runs
lua/s2ai_probe.lua from this repository, and writes the result as a loose file under data\ (loose files
override the packs). Nothing in the game's packs is modified; -Uninstall deletes the loose files again.

Targets:
  battle_scripted.lua         UI/event scripting state, loaded for every battle   (loader appended, env "ui")
  <historical>.battle_script  battle-script state of the menu historical battles  (loader prepended, env "battle")

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

$Targets = @(
    @{ Path = 'battle_scripted.lua'; Mode = 'append'; Env = 'ui' }
) + (@('kawanakajima\kawanakajima_battle', 'nagashino\nagashino_battle', 'okehazama\okehazama_battle',
        'sekigahara\sekigahara_battle', 'nagashima\nagashima_battle') | ForEach-Object {
        @{ Path = "shogun_historical_battles\$_.battle_script"; Mode = 'prepend'; Env = 'battle' }
    })

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

$ProbePath = Join-Path $Repo 'lua\s2ai_probe.lua'

# "ui": dofile() the probe from the repo, so edits apply without reinstalling (dofile + io proved to work there).
# "battle": paste the probe source in, wrapped in pcall - the battle-script state may lack dofile/io, and an
#           error at the top of a historical battle's script must never stop that script from running.
function Get-Loader($envName) {
    $report = @"
	if not ok then
		pcall(function()
			local f = io.open(S2AI_ROOT .. "/runtime/probe.log", "a")
			f:write("LOAD ERROR [$envName]: ", tostring(err), "\n"); f:close()
		end)
	end
"@
    if ($envName -eq 'battle') {
        $src = [IO.File]::ReadAllText($ProbePath)
        $body = "local ok, err = pcall(function()`n$src`nend)`n$report"
    } else {
        $body = "local ok, err = pcall(dofile, S2AI_ROOT .. `"/lua/s2ai_probe.lua`")`n$report"
    }
    @"
$Marker (tools/install.ps1; remove with -Uninstall)
S2AI_ROOT = "$RepoLua"
S2AI_ENV = "$envName"
do
$body
end
-- end of shogun2-battle-ai loader
"@
}

foreach ($d in $DataDirs) {
    if ($Uninstall) {
        foreach ($t in $Targets) {
            $target = Join-Path $d $t.Path
            if ((Test-Path $target) -and (Select-String -Path $target -SimpleMatch $Marker -Quiet)) {
                Remove-Item $target; Write-Host "removed  $target"
                $dir = Split-Path $target
                while ($dir -ne $d -and (Test-Path $dir) -and -not (Get-ChildItem $dir -Force)) { Remove-Item $dir; $dir = Split-Path $dir }
            } elseif (Test-Path $target) {
                Write-Warning "left alone (not ours): $target"
            }
        }
        continue
    }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("s2ai_" + [guid]::NewGuid())
    $filter = '^(' + (($Targets | ForEach-Object { [regex]::Escape($_.Path) }) -join '|') + ')$'
    & (Join-Path $PSScriptRoot 'packtool.ps1') -Pack (Join-Path $d 'data.pack') -Filter $filter -OutDir $tmp 6>$null | Out-Null
    foreach ($t in $Targets) {
        $target = Join-Path $d $t.Path
        if ((Test-Path $target) -and -not (Select-String -Path $target -SimpleMatch $Marker -Quiet)) {
            Write-Warning "skipped: $target exists and was not written by this tool"
            continue
        }
        $orig = Join-Path $tmp $t.Path
        if (-not (Test-Path $orig)) { Write-Warning "$($t.Path) not found in $d\data.pack"; continue }
        $text = [IO.File]::ReadAllText($orig).TrimEnd()
        $loader = Get-Loader $t.Env
        $out = if ($t.Mode -eq 'append') { $text + "`n`n" + $loader } else { $loader + "`n`n" + $text }
        New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
        [IO.File]::WriteAllText($target, ($out -replace "`r?`n", "`r`n") + "`r`n", (New-Object Text.UTF8Encoding($false)))
        Write-Host "installed $target"
    }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not $Uninstall) { New-Item -ItemType Directory -Force (Join-Path $Repo 'runtime') | Out-Null }
