param(
    [string]$SkillsRoot = (Join-Path $env:USERPROFILE '.codex\skills'),
    [string]$DependencyModules = (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules')
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source = (Resolve-Path -LiteralPath (Join-Path $repo 'skills\netapp-pa-report')).Path
$target = [IO.Path]::GetFullPath((Join-Path $SkillsRoot 'netapp-pa-report'))
if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) { throw 'Skill source missing.' }
New-Item -ItemType Directory -Path $SkillsRoot -Force | Out-Null
if (Test-Path -LiteralPath $target) {
    $existing = Get-Item -LiteralPath $target -Force
    if ($existing.LinkType -ne 'Junction' -or
        [IO.Path]::GetFullPath([string]$existing.Target) -ne $source) {
        throw "Existing skill differs; no files were replaced: $target"
    }
} else {
    New-Item -ItemType Junction -Path $target -Target $source | Out-Null
}
$modules = Join-Path $repo 'node_modules'
if (-not (Test-Path -LiteralPath $modules)) {
    if (-not (Test-Path -LiteralPath (Join-Path $DependencyModules '@oai\artifact-tool'))) {
        throw 'Bundled Artifact Tool not found. Skill installed; Node dependencies still require configuration.'
    }
    New-Item -ItemType Junction -Path $modules -Target $DependencyModules | Out-Null
}
Write-Output "SKILL $target"
Write-Output 'Installed source-linked entry only. Run scripts/run.py --doctor to validate runtime.'
