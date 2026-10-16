[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$FixtureManifest,
    [ValidateSet(1,2)][int]$Concurrency=1, [ValidateRange(1,20)][int]$Repetitions=5)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptConcurrency.ps1')
$manifest = Get-Content -LiteralPath $FixtureManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$exe = [IO.Path]::GetFullPath([string]$manifest.executable)
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $manifest.executable_sha256) { throw 'EXECUTABLE_HASH_MISMATCH' }
$fixtureRoot = [IO.Path]::GetFullPath([string]$manifest.fixture_root).TrimEnd('\')
$fixtures = @($manifest.fixtures)
foreach ($fixture in $fixtures) {
    $inputPath = [IO.Path]::GetFullPath([string]$fixture.input)
    if (-not $inputPath.StartsWith($fixtureRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'FIXTURE_OUTSIDE_ROOT' }
    Assert-DocMindNoReparsePoint -LiteralPath $inputPath -Boundary $fixtureRoot -Name 'Probe fixture'
    if ((Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash -ne $fixture.ciphertext_sha256) { throw 'FIXTURE_HASH_MISMATCH' }
}
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/decrypt-concurrency-probe'))
$gate = Enter-DocMindDecryptGate -ControlRoot ([string]$manifest.control_root) -DeadlineUtc ([DateTimeOffset]::UtcNow.AddSeconds(10)) -ValidateLease { }
$confirmed = $true
try {
    $trials = @()
    for ($i=0; $i -lt $Repetitions; $i++) {
        $result = Invoke-DocMindDecryptTrial -Executable $exe -Fixtures $fixtures -WorkRoot $root -Concurrency $Concurrency -TimeoutSeconds 120
        $trials += $result
        $confirmed = $result.process_exit_confirmed
        if (-not $result.success) { break }
    }
    [pscustomobject]@{executable_sha256=$manifest.executable_sha256;trials=$trials} | ConvertTo-Json -Depth 6
} catch {
    if ($_.Exception.Message -eq 'PROCESS_TERMINATION_FAILED') { $confirmed = $false }
    throw
} finally { Exit-DocMindDecryptGate -Gate $gate -ProcessExitConfirmed $confirmed }
