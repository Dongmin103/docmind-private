Set-StrictMode -Version Latest

function Invoke-DocMindDecryptTrial {
    param([string]$Executable, [object[]]$Fixtures, [string]$WorkRoot,
        [ValidateRange(1,2)][int]$Concurrency, [ValidateRange(1,3600)][int]$TimeoutSeconds)
    if ($Fixtures.Count -lt 1) { throw 'EMPTY_FIXTURES' }
    $boundary = [IO.Path]::GetFullPath($WorkRoot)
    $directory = Join-Path $boundary ([Guid]::NewGuid().ToString('n'))
    Assert-DocMindNoReparsePoint -LiteralPath $boundary -Boundary $boundary -Name 'Probe root'
    [void][IO.Directory]::CreateDirectory($directory)
    Protect-DocMindJobDirectory -LiteralPath $directory
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $running = [Collections.Generic.List[object]]::new()
    $results = [Collections.Generic.List[object]]::new()
    $next = 0; $peak = 0; $exited = $true; $cleaned = $false
    try {
        while ($next -lt $Fixtures.Count -or $running.Count -gt 0) {
            while ($next -lt $Fixtures.Count -and $running.Count -lt $Concurrency) {
                $fixture = $Fixtures[$next]
                if ([string]$fixture.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'INVALID_EXPECTED_HASH' }
                $inputPath = [IO.Path]::GetFullPath([string]$fixture.input)
                $outputPath = Join-Path $directory ($next.ToString() + '.output')
                $child = [DocMind.DecryptChild]::new($Executable, $inputPath, $outputPath)
                $running.Add([pscustomobject]@{child=$child;output=$outputPath;hash=[string]$fixture.sha256;ordinal=$next;start=$clock.Elapsed.TotalSeconds})
                $next++
            }
            # Count living processes, not merely submitted requests.
            $alive = @($running | Where-Object { -not $_.child.Exited }).Count
            $peak = [Math]::Max($peak, $alive)
            foreach ($item in @($running.ToArray())) {
                $errorCode = $null
                $timeout = $clock.Elapsed.TotalSeconds - $item.start -ge $TimeoutSeconds
                if (-not $item.child.Exited -and -not $timeout) { continue }
                if ($timeout -and -not $item.child.Exited) {
                    if (-not $item.child.Stop()) { $exited = $false; throw 'PROCESS_TERMINATION_FAILED' }
                    $errorCode = 'TIMEOUT'
                }
                $item.child.Process.WaitForExit()
                $exitCode = $item.child.Process.ExitCode
                if (-not $errorCode -and $exitCode -ne 0) { $errorCode = 'NONZERO_EXIT' }
                if (-not $errorCode -and -not (Test-Path -LiteralPath $item.output -PathType Leaf)) { $errorCode = 'OUTPUT_MISSING' }
                if (-not $errorCode -and (Get-FileHash -LiteralPath $item.output -Algorithm SHA256).Hash -ne $item.hash) { $errorCode = 'HASH_MISMATCH' }
                $results.Add([pscustomobject]@{ordinal=$item.ordinal;start_seconds=$item.start;end_seconds=$clock.Elapsed.TotalSeconds;exit_code=$exitCode;error=$errorCode;hash_match=($null -eq $errorCode)})
                $item.child.Dispose(); [void]$running.Remove($item)
            }
            if ($running.Count) { Start-Sleep -Milliseconds 10 }
        }
    } finally {
        foreach ($item in $running) {
            try { if (-not $item.child.Stop()) { $exited = $false } } finally { $item.child.Dispose() }
        }
        if ($exited) {
            $resolved = [IO.Path]::GetFullPath($directory)
            if (-not $resolved.StartsWith($boundary.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'UNSAFE_PROBE_CLEANUP' }
            Assert-DocMindNoReparsePoint -LiteralPath $resolved -Boundary $boundary -Name 'Probe output'
            Remove-Item -LiteralPath $resolved -Recurse -Force
            $cleaned = -not (Test-Path -LiteralPath $resolved)
        }
    }
    return [pscustomobject]@{concurrency=$Concurrency;seconds=$clock.Elapsed.TotalSeconds;peak_processes=$peak;success=($results.Count -eq $Fixtures.Count -and @($results | Where-Object error).Count -eq 0);process_exit_confirmed=$exited;cleanup_complete=$cleaned;results=@($results.ToArray())}
}
