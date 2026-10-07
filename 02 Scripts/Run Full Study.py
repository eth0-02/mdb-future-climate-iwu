"""Download the complete available ensemble, then calculate the MDB study.

Re-running is safe: the downloader reads and validates completed source files.
Each analysis writes a new dated directory and leaves the pilot untouched.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import msvcrt


def main():
    root = Path(__file__).resolve().parents[1]
    logs = root / '03 Outputs' / 'Logs'
    logs.mkdir(parents=True, exist_ok=True)
    lock = (logs / 'Full Study.lock').open('a+b')
    lock.seek(0)
    if lock.read(1) == b'':
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print('The full study is already running. No duplicate job started.', flush=True)
        return 1

    def status(stage, **extra):
        record = {'stage': stage, 'pid': os.getpid(),
                  'updated_utc': datetime.now(timezone.utc).isoformat(), **extra}
        destination = logs / 'Full Study Status.json'
        temporary = destination.with_suffix('.pending')
        temporary.write_text(json.dumps(record, indent=2))
        temporary.replace(destination)
        print(json.dumps(record), flush=True)

    try:
        for attempt in range(1, 6):
            status('Downloading and validating annual NASA subsets', attempt=attempt)
            result = subprocess.run([sys.executable, str(root / '02 Scripts' / 'Download NASA Subsets.py'),
                                     '--root', str(root), '--all-years'])
            if result.returncode == 0:
                break
            if attempt < 5:
                time.sleep(60)
        status('Processing available complete climate years', download_exit_code=result.returncode)
        analysis = subprocess.run([sys.executable, str(root / '02 Scripts' / 'Run Ensemble.py'), '--root', str(root)])
        status('Finished; inspect completeness reports before using results',
               download_exit_code=result.returncode, analysis_exit_code=analysis.returncode,
               known_source_gap='CESM2 tasmax and tasmin are not available in the audited archive')
        return result.returncode or analysis.returncode
    except BaseException as exc:
        status('Stopped or failed', error=str(exc))
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
