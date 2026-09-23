"""Bounded FFmpeg execution with console/file progress via the caller's log."""
import queue
import subprocess
import threading
import time


def execute(ffmpeg, args, label, log, error_path, stall_seconds=120, deadline_seconds=1800):
    with open(error_path, 'a', encoding='utf-8') as err:
        proc = subprocess.Popen([ffmpeg, '-hide_banner', '-nostdin', '-y', '-progress', 'pipe:1',
                                 '-stats_period', '2', '-nostats', *args], stdout=subprocess.PIPE,
                                stderr=err, text=True, encoding='utf-8', errors='replace')
        lines = queue.Queue()
        def read():
            for line in proc.stdout:
                lines.put(line.strip())
            lines.put(None)
        threading.Thread(target=read, daemon=True).start()
        started = advanced = reported = time.monotonic()
        last_time = -1
        values = {}
        try:
            while True:
                now = time.monotonic()
                if now-started > deadline_seconds or now-advanced > stall_seconds:
                    raise TimeoutError(f'{label}: deadline/stall exceeded; see {error_path}')
                try:
                    line = lines.get(timeout=1)
                except queue.Empty:
                    if now-reported >= 15:
                        log.emit('WAIT', f'{label}; no timeline advance for {now-advanced:.0f}s')
                        reported = now
                    continue
                if line is None:
                    break
                key, sep, value = line.partition('=')
                if sep:
                    values[key] = value
                if key == 'progress':
                    elapsed = int(values.get('out_time_us', '0')) if values.get('out_time_us', '').lstrip('-').isdigit() else 0
                    if elapsed > last_time:
                        last_time = elapsed
                        advanced = time.monotonic()
                    log.emit('FFMPEG', f'{label}; frame={values.get("frame","-")}; time={values.get("out_time","-")}; speed={values.get("speed","-")}; no_advance={time.monotonic()-advanced:.0f}s')
                    reported = time.monotonic()
            rc = proc.wait(timeout=10)
            if rc:
                raise RuntimeError(f'{label}: FFmpeg exit={rc}; see {error_path}')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            proc.stdout.close()
