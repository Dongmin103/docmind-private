[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'DocMindUEncryptorHostWorker.Common.ps1')
. (Join-Path $PSScriptRoot 'DocMindDecryptGate.ps1')
$implementation = Join-Path $PSScriptRoot 'DocMindDecryptConcurrency.ps1'
if (-not (Test-Path $implementation)) { throw 'Missing decrypt concurrency probe implementation' }
. $implementation
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ('../../.local/decrypt-concurrency-tests/' + [Guid]::NewGuid().ToString('n'))))
[void][IO.Directory]::CreateDirectory($root)
try {
    $exe = Join-Path $root 'fixture.exe'
    Add-Type -TypeDefinition 'using System; using System.IO; using System.Threading; public class ConcurrentFixture { public static int Main(string[] a) { Thread.Sleep(int.Parse(File.ReadAllText(a[0]))); File.WriteAllText(a[1], "fixture"); return 0; } }' -OutputAssembly $exe -OutputType ConsoleApplication
    $inputFile = Join-Path $root 'input.enc'; [IO.File]::WriteAllText($inputFile, '400')
    $reference = Join-Path $root 'reference'; [IO.File]::WriteAllText($reference, 'fixture')
    $hash = (Get-FileHash $reference -Algorithm SHA256).Hash
    $fixtures = @(@{input=$inputFile;sha256=$hash}, @{input=$inputFile;sha256=$hash})
    $serial = Invoke-DocMindDecryptTrial -Executable $exe -Fixtures $fixtures -WorkRoot (Join-Path $root 'work') -Concurrency 1 -TimeoutSeconds 5
    $parallel = Invoke-DocMindDecryptTrial -Executable $exe -Fixtures $fixtures -WorkRoot (Join-Path $root 'work') -Concurrency 2 -TimeoutSeconds 5
    Assert-True ($serial.success -and $parallel.success) 'Known-pair outputs failed validation.'
    Assert-True ($serial.peak_processes -eq 1 -and $parallel.peak_processes -eq 2) 'Actual process overlap was not measured.'
    Assert-True ($parallel.cleanup_complete -and $serial.cleanup_complete) 'Plaintext remained after probe.'
    $bad = Invoke-DocMindDecryptTrial -Executable $exe -Fixtures @(@{input=$inputFile;sha256=('0'*64)}) -WorkRoot (Join-Path $root 'work') -Concurrency 1 -TimeoutSeconds 5
    Assert-True (-not $bad.success -and $bad.results[0].error -eq 'HASH_MISMATCH') 'Incorrect output accepted.'
    [IO.File]::WriteAllText($inputFile, '30000')
    $timeout = Invoke-DocMindDecryptTrial -Executable $exe -Fixtures $fixtures -WorkRoot (Join-Path $root 'work') -Concurrency 2 -TimeoutSeconds 1
    Assert-True (-not $timeout.success -and $timeout.cleanup_complete -and $timeout.process_exit_confirmed) 'Timeout did not stop both children and clean output.'
    Write-Output 'PASS: independent outputs, real overlap, hash mismatch, timeout and cleanup.'
} finally {
    if (-not $root.StartsWith([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../.local/decrypt-concurrency-tests/')), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup' }
    Remove-Item -LiteralPath $root -Recurse -Force
}
