using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

namespace DocMind
{
    // Supervise metadata independently of PowerShell's synchronous file hashes.
    // The helper's host mutex guarantees that only one process performs work.
    public sealed class FolderSupervisor : IDisposable
    {
        private readonly ManualResetEvent stop = new ManualResetEvent(false);
        private readonly Thread thread;
        private readonly string shell, script, config, identity;
        private DateTime lastLaunch = DateTime.MinValue;

        public FolderSupervisor(string shell, string script, string config, string stateRoot)
        {
            this.shell = shell; this.script = script; this.config = config;
            identity = Path.Combine(stateRoot, "folder-helper.json");
            thread = new Thread(Run); thread.IsBackground = true; thread.Start();
        }

        private static string Quote(string value)
        {
            if (value.IndexOf('"') >= 0) throw new ArgumentException("Invalid supervisor path.");
            return "\"" + value + "\"";
        }

        private bool Running()
        {
            try
            {
                // Identity is a compact two-number record. Avoid a dependency
                // on a JSON library in the native Windows watcher callbacks.
                string json = File.ReadAllText(identity);
                var pid = System.Text.RegularExpressions.Regex.Match(json, "\"pid\"\\s*:\\s*(\\d+)");
                var ticks = System.Text.RegularExpressions.Regex.Match(json, "\"started_ticks\"\\s*:\\s*(\\d+)");
                if (!pid.Success || !ticks.Success) return false;
                using (Process process = Process.GetProcessById(Int32.Parse(pid.Groups[1].Value)))
                    return !process.HasExited && process.StartTime.ToUniversalTime().Ticks == Int64.Parse(ticks.Groups[1].Value);
            }
            catch { return false; }
        }

        private void Run()
        {
            while (!stop.WaitOne(500))
            {
                if (Running() || DateTime.UtcNow < lastLaunch.AddSeconds(2)) continue;
                lastLaunch = DateTime.UtcNow;
                try
                {
                    var start = new ProcessStartInfo(shell,
                        "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File " + Quote(script) + " -ConfigPath " + Quote(config));
                    start.UseShellExecute = false; start.CreateNoWindow = true;
                    start.WindowStyle = ProcessWindowStyle.Hidden;
                    using (Process child = Process.Start(start)) { }
                }
                catch { /* Retry without blocking source event capture. */ }
            }
        }

        public void Dispose()
        {
            stop.Set(); thread.Join(); stop.Dispose();
        }
    }
}
