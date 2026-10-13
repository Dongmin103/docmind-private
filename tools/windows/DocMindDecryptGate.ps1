# Windows PowerShell 5.1. The job is assigned before the suspended child runs.
# Closing the parent's last job handle kills every descendant, including when
# the parent is forcibly terminated between starting and publishing its PID.
if (-not ('DocMind.DecryptChild' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
namespace DocMind {
 public sealed class DecryptChild : IDisposable {
  [StructLayout(LayoutKind.Sequential)] struct IO_COUNTERS { public ulong a,b,c,d,e,f; }
  [StructLayout(LayoutKind.Sequential)] struct BASIC_LIMIT { public long perProcess,perJob; public uint flags; public UIntPtr min,max; public uint active; public UIntPtr affinity; public uint priority,scheduling; }
  [StructLayout(LayoutKind.Sequential)] struct EXTENDED_LIMIT { public BASIC_LIMIT basic; public IO_COUNTERS io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob; }
  [StructLayout(LayoutKind.Sequential)] struct ACCOUNTING { public long a,b,c,d; public uint faults,total,active,terminated; }
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct STARTUP { public uint cb; public string reserved,desktop,title; public uint x,y,xsize,ysize,xchars,ychars,fill,flags; public ushort show,reserved2; public IntPtr bytes,input,output,error; }
  [StructLayout(LayoutKind.Sequential)] struct STARTUP_EX { public STARTUP startup; public IntPtr attributes; }
  [StructLayout(LayoutKind.Sequential)] struct PROCESS_INFO { public IntPtr process,thread; public uint pid,tid; }
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr security,string name);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,ref EXTENDED_LIMIT info,uint size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int kind,out ACCOUNTING info,uint size,IntPtr length);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string app,System.Text.StringBuilder command,IntPtr ps,IntPtr ts,bool inherit,uint flags,IntPtr env,string directory,ref STARTUP_EX start,out PROCESS_INFO info);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,uint flags,ref IntPtr size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
  [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job,uint code);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
  [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr thread);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  IntPtr job; public Process Process { get; private set; }
  public DecryptChild(string exe,string source,string output) {
   job=CreateJobObject(IntPtr.Zero,null); if(job==IntPtr.Zero) throw new Win32Exception();
   var limits=new EXTENDED_LIMIT(); limits.basic.flags=0x2000;
   if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(limits))) { Dispose(); throw new Win32Exception(); }
   var startup=new STARTUP_EX(); startup.startup.cb=(uint)Marshal.SizeOf(startup); PROCESS_INFO child;
   var command=new System.Text.StringBuilder("\""+exe+"\" \""+source+"\" \""+output+"\" /dec");
   IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
   startup.attributes=Marshal.AllocHGlobal(size); var jobPointer=Marshal.AllocHGlobal(IntPtr.Size); bool initialized=false;
   try {
    if(!InitializeProcThreadAttributeList(startup.attributes,1,0,ref size)) throw new Win32Exception();
    initialized=true; Marshal.WriteIntPtr(jobPointer,job);
    // JOB_LIST makes membership atomic with process creation, closing the
    // parent-death gap between CreateProcess and AssignProcessToJobObject.
    if(!UpdateProcThreadAttribute(startup.attributes,0,new IntPtr(0x2000d),jobPointer,new IntPtr(IntPtr.Size),IntPtr.Zero,IntPtr.Zero)) throw new Win32Exception();
    if(!CreateProcess(exe,command,IntPtr.Zero,IntPtr.Zero,false,0x08080004,IntPtr.Zero,null,ref startup,out child)) throw new Win32Exception();
   } catch { Dispose(); throw; }
   finally { if(initialized) DeleteProcThreadAttributeList(startup.attributes); Marshal.FreeHGlobal(startup.attributes); Marshal.FreeHGlobal(jobPointer); }
   try {
    Process=Process.GetProcessById((int)child.pid);
    var processHandle=Process.Handle;
    if(ResumeThread(child.thread)==0xffffffff) { TerminateJobObject(job,1); throw new Win32Exception(); }
   } catch { Dispose(); throw; }
   finally { CloseHandle(child.thread); CloseHandle(child.process); }
  }
  public bool Exited { get { ACCOUNTING info; if(!QueryInformationJobObject(job,1,out info,(uint)Marshal.SizeOf(typeof(ACCOUNTING)),IntPtr.Zero)) throw new Win32Exception(); return info.active==0; } }
  public bool Wait(int milliseconds) { var timer=Stopwatch.StartNew(); do { if(Exited) return true; System.Threading.Thread.Sleep(20); } while(timer.ElapsedMilliseconds<milliseconds); return Exited; }
  public bool Stop() { if(!Exited && !TerminateJobObject(job,1)) return false; return Wait(5000); }
  public void Dispose() { if(job!=IntPtr.Zero) { CloseHandle(job); job=IntPtr.Zero; } if(Process!=null) Process.Dispose(); }
 }
}
'@
}

function Write-DocMindGateRecord {
    param([string]$Path, $Record)
    $temp = $Path + '.' + [Guid]::NewGuid().ToString('n') + '.tmp'
    [IO.File]::WriteAllText($temp, ($Record | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) {
        $backup = $temp + '.bak'
        try { [IO.File]::Replace($temp, $Path, $backup, $true) }
        finally { if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force } }
    } else { [IO.File]::Move($temp, $Path) }
}

function Set-DocMindGateBlocked {
    param([string]$ControlRoot, [string]$Reason)
    Write-DocMindGateRecord -Path (Join-Path $ControlRoot 'blocked.json') -Record @{ reason = $Reason; utc = [DateTimeOffset]::UtcNow.ToString('o') }
}

function Assert-DocMindDecryptControlSafe {
    param([string]$ControlRoot, [switch]$ReadOnly)
    if (Test-Path -LiteralPath (Join-Path $ControlRoot 'blocked.json')) { throw 'DECRYPT_GATE_BLOCKED' }
    $ownerPath = Join-Path $ControlRoot 'owner.json'
    if (-not (Test-Path -LiteralPath $ownerPath)) { return }
    try {
        $owner = Get-Content -LiteralPath $ownerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($owner.phase -ne 'running' -or -not $owner.child_pid -or -not $owner.child_started_utc -or -not $owner.executable_sha256) { throw 'INCOMPLETE_OWNER' }
        $child = Get-Process -Id ([int]$owner.child_pid) -ErrorAction SilentlyContinue
        # A reused PID is uncertain, and must never be killed or assumed safe.
        if ($null -ne $child) { throw 'OWNER_PROCESS_PRESENT' }
        if (-not $ReadOnly) { Remove-Item -LiteralPath $ownerPath -Force }
    } catch {
        if ($ReadOnly) { throw 'DECRYPT_ACTIVE_OR_UNRESOLVED' }
        Set-DocMindGateBlocked -ControlRoot $ControlRoot -Reason 'OWNER_EXIT_UNCONFIRMED'
        throw 'DECRYPT_GATE_BLOCKED'
    }
}

function Enter-DocMindDecryptGate {
    param([string]$ControlRoot, [DateTimeOffset]$DeadlineUtc, [scriptblock]$ValidateLease)
    [IO.Directory]::CreateDirectory($ControlRoot) | Out-Null
    Assert-DocMindNoReparsePoint -LiteralPath $ControlRoot -Boundary $ControlRoot -Name 'Control root'
    Protect-DocMindJobDirectory -LiteralPath $ControlRoot
    $mutex = [Threading.Mutex]::new($false, 'Global\DocMind-uEncryptor2-SingleRun')
    $acquired = $false
    try {
        while (-not $acquired) {
            $validationClock = [Diagnostics.Stopwatch]::StartNew()
            if ($ValidateLease) { & $ValidateLease | Out-Null }
            if (Test-Path -LiteralPath (Join-Path $ControlRoot 'blocked.json')) { throw 'DECRYPT_GATE_BLOCKED' }
            $remaining = ($DeadlineUtc - [DateTimeOffset]::UtcNow).TotalMilliseconds
            if ($remaining -le 0) { throw 'PREVIEW_DECRYPT_BUSY' }
            $pollWait = [Math]::Max(0, 1000 - $validationClock.ElapsedMilliseconds)
            try { $acquired = $mutex.WaitOne([int][Math]::Min($pollWait, $remaining)) }
            catch [Threading.AbandonedMutexException] {
                $acquired = $true
                Set-DocMindGateBlocked -ControlRoot $ControlRoot -Reason 'ABANDONED_MUTEX'
            }
        }
        Assert-DocMindDecryptControlSafe -ControlRoot $ControlRoot
        if ($ValidateLease) { & $ValidateLease | Out-Null }
        if ([DateTimeOffset]::UtcNow -ge $DeadlineUtc) { throw 'PREVIEW_DECRYPT_BUSY' }
        return [pscustomobject]@{ Mutex = $mutex; ControlRoot = $ControlRoot; Released = $false; ProcessExitConfirmed = $true }
    } catch {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose(); throw
    }
}

function Invoke-DocMindDecryptProcess {
    param($Gate, [string]$Executable, [string]$SourceFile, [string]$OutputFile, [int]$TimeoutSeconds)
    if ($Gate.Released) { throw 'DECRYPT_GATE_RELEASED' }
    $ownerPath = Join-Path $Gate.ControlRoot 'owner.json'
    $Gate.ProcessExitConfirmed = $false
    $child = $null
    $owner = @{ parent_pid = $PID; parent_started_utc = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o'); executable_sha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant(); phase = 'starting' }
    Write-DocMindGateRecord -Path $ownerPath -Record $owner
    try {
        $child = [DocMind.DecryptChild]::new($Executable, $SourceFile, $OutputFile)
        $owner.child_pid = $child.Process.Id
        $owner.child_started_utc = $child.Process.StartTime.ToUniversalTime().ToString('o')
        $owner.phase = 'running'
        Write-DocMindGateRecord -Path $ownerPath -Record $owner
        if (-not $child.Wait($TimeoutSeconds * 1000)) { throw 'TIMEOUT_OR_INTERACTIVE_PROMPT' }
        $Gate.ProcessExitConfirmed = $true
        $child.Process.WaitForExit()
        if ($child.Process.ExitCode -ne 0) { throw 'NONZERO_EXIT' }
    } catch {
        $failure = $_
        if ($null -ne $child) { $Gate.ProcessExitConfirmed = Stop-DocMindDecryptChild -Child $child }
        # Creation failure can leave an untracked suspended process; fail closed.
        if (-not $Gate.ProcessExitConfirmed) {
            Set-DocMindGateBlocked -ControlRoot $Gate.ControlRoot -Reason 'PROCESS_TERMINATION_FAILED'
            throw 'PROCESS_TERMINATION_FAILED'
        }
        throw $failure
    } finally {
        if ($child) { $child.Dispose() }
        if ($Gate.ProcessExitConfirmed -and (Test-Path -LiteralPath $ownerPath)) { Remove-Item -LiteralPath $ownerPath -Force }
    }
}

function Stop-DocMindDecryptChild {
    param($Child)
    try { return $Child.Stop() } catch { return $false }
}

function Exit-DocMindDecryptGate {
    param($Gate, [bool]$ProcessExitConfirmed)
    if ($null -eq $Gate -or $Gate.Released) { return }
    if (-not $ProcessExitConfirmed) { Set-DocMindGateBlocked -ControlRoot $Gate.ControlRoot -Reason 'PROCESS_TERMINATION_FAILED' }
    # Persistent blocking precedes release, so another owner cannot launch.
    $Gate.Mutex.ReleaseMutex(); $Gate.Mutex.Dispose(); $Gate.Released = $true
}
