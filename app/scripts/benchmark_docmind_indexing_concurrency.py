"""Measure an explicitly supplied indexing command; never print its output/body.

The command must return only after search visibility and cleanup are verified.
This tool reports command wall time, not inferred sums of overlapping stages.
"""
import argparse
import json
import statistics
import subprocess
import threading
import time
from pathlib import Path


def summarize(exit_code, elapsed_seconds, samples, metadata):
    samples = [s for s in samples if 0 <= s['elapsed'] <= elapsed_seconds]
    result = {'valid': exit_code == 0 and bool(samples), 'exit_code': exit_code,
              'elapsed_seconds': elapsed_seconds, 'sample_count': len(samples),
              'metadata': metadata, 'scope': 'supplied command wall time; device-wide GPU', 'samples': samples}
    for key in ('gpu_percent', 'vram_mib', 'power_w'):
        values = [s[key] for s in samples]
        result[key] = {'mean': statistics.mean(values), 'max': max(values)} if values else None
    return result


def compare(baseline, candidate):
    if min(len(baseline), len(candidate)) < 3:
        raise ValueError('At least three runs per variant required')
    runs = baseline + candidate
    if not all(r['valid'] for r in runs):
        raise ValueError('Invalid runs cannot establish improvement')
    fields = ('manifest_sha256', 'model', 'batch')
    if any(not all(k in r['metadata'] for k in fields) for r in runs):
        raise ValueError('Comparison identity missing')
    if len({tuple(r['metadata'][k] for k in fields) for r in runs}) != 1:
        raise ValueError('Different workloads or model configuration')
    a = statistics.median(r['elapsed_seconds'] for r in baseline)
    b = statistics.median(r['elapsed_seconds'] for r in candidate)
    if a <= 0 or b <= 0:
        raise ValueError('Positive durations required')
    return {'baseline_median_seconds': a, 'candidate_median_seconds': b,
            'improvement_percent': round((1-b/a)*100, 3)}


def measure(command, metadata, timeout):
    stop = threading.Event()
    samples = []
    start = time.perf_counter()

    def sample():
        while not stop.is_set():
            try:
                out = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,power.draw',
                                      '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3, check=True)
                values = [float(x) for x in out.stdout.splitlines()[0].split(',')]
                samples.append(dict(zip(('gpu_percent', 'vram_mib', 'power_w'), values), elapsed=time.perf_counter()-start))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
            stop.wait(.2)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        # No shell interpretation. Avoid collecting document bodies or unbounded logs.
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # The explicit command remains owned by its caller; do not report a
            # killed wrapper as successful cleanup of remote indexing jobs.
            process.kill()
            process.wait()
            code = 124
    finally:
        elapsed = time.perf_counter()-start
        stop.set()
        thread.join(timeout=4)
    return summarize(code, elapsed, samples, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=3600)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.timeout <= 0:
        parser.error('A command and positive timeout are required')
    metadata = json.loads(args.metadata.read_text(encoding='utf-8-sig'))
    result = measure(command, metadata, args.timeout)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
