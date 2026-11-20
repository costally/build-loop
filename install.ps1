param([string]$Root = 'C:\build-loop')
$ErrorActionPreference = 'Stop'
$sourcePath = [IO.Path]::GetFullPath($PSScriptRoot)
$targetRoot = [IO.Path]::GetFullPath($Root)
$targetSystem = Join-Path $targetRoot 'system'
if ($sourcePath -eq $targetSystem) { throw 'Run the installed build-loop.cmd directly.' }
if (Test-Path -LiteralPath $targetSystem) { throw "Existing system preserved: $targetSystem. Choose a separate root for this installation." }
& python -c "import sys,sqlite3; assert sys.version_info >= (3,13), 'Python 3.13+ required'"
if ($LASTEXITCODE -ne 0) { throw 'Python runtime check failed.' }
$includedPaths = @('build_loop','templates','skills','docs','.codex','AGENTS.md','workspace_hook.py','launch.py','control.py','hook_entry.py','build-loop.cmd','README.md','LICENSE','THIRD_PARTY.md')
foreach ($entry in $includedPaths) {
 if (-not (Test-Path -LiteralPath (Join-Path $sourcePath $entry))) { throw "Missing release file: $entry" }
}
New-Item -ItemType Directory -Path $targetSystem | Out-Null
foreach ($entry in $includedPaths) {
 Copy-Item -LiteralPath (Join-Path $sourcePath $entry) -Destination (Join-Path $targetSystem $entry) -Recurse
}
Write-Output "Installed: $targetSystem\build-loop.cmd"
Write-Output 'Existing projects and global Codex settings were preserved.'
