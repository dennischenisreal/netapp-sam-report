param(
    [Parameter(Mandatory = $true)]
    [string]$ReportsRoot,
    [switch]$ReplaceExisting
)

$ErrorActionPreference = 'Stop'
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'report_output_config.json') -Raw | ConvertFrom-Json
$destinationRoot = [IO.Path]::GetFullPath($config.output_root)
$sourceRoot = (Resolve-Path -LiteralPath $ReportsRoot).Path
$models = @(Get-ChildItem -LiteralPath $sourceRoot -Filter 'report_model.json' -File -Recurse)
if ($models.Count -eq 0) { throw 'No report models found.' }

$plan = @(
    foreach ($modelFile in $models) {
        $model = Get-Content -LiteralPath $modelFile.FullName -Raw | ConvertFrom-Json
        if ($model.model_version -ne 'performance-report-v6-refined') {
            throw "Only the approved resource-first format can be published: $($modelFile.FullName)"
        }
        $site = [string]$model.customer
        if ([string]::IsNullOrWhiteSpace($site) -or $site -in @('.', '..') -or
            $site.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
            $site -ne $site.TrimEnd(' ', '.')) {
            throw "Invalid site name: $site"
        }
        $filename = [string]$model.output_filename
        if ([IO.Path]::GetFileName($filename) -ne $filename -or
            $filename -notmatch '_(?<month>20\d{4})(?:_.*)?\.xlsx$') {
            throw "Invalid workbook filename: $filename"
        }
        $month = $Matches.month
        $source = Join-Path $modelFile.DirectoryName $filename
        $verification = Get-Content -LiteralPath (Join-Path $modelFile.DirectoryName 'verification_compact.json') -Raw |
            ConvertFrom-Json
        $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        if ($hash -ne $verification.sha256 -or $verification.formula_errors -ne 0) {
            throw "Report changed since verification: $source"
        }
        $destination = [IO.Path]::GetFullPath(
            (Join-Path (Join-Path $destinationRoot $site) "PA_Report_${site}_${month}.xlsx"))
        if (-not $destination.StartsWith($destinationRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Destination escapes the configured output directory: $destination"
        }
        if ((Test-Path -LiteralPath $destination) -and -not $ReplaceExisting -and
            (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $hash) {
            throw "Destination already exists. Use -ReplaceExisting only after approval: $destination"
        }
        [PSCustomObject]@{ Site = $site; Source = $source; Destination = $destination; SHA256 = $hash }
    }
)
if (@($plan.Destination | Select-Object -Unique).Count -ne $plan.Count) {
    throw 'Multiple reports target the same destination.'
}

foreach ($item in $plan) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $item.Destination) -Force | Out-Null
    Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
    if ((Get-FileHash -LiteralPath $item.Destination -Algorithm SHA256).Hash -ne $item.SHA256) {
        throw "Published file hash mismatch: $($item.Destination)"
    }
    Write-Output "PUBLISHED $($item.Site): $($item.Destination)"
}
