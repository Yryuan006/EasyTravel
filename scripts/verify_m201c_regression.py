"""Reuse the accepted isolated 454-test runner, redirecting ALL evidence to M2-01c.

The runner still creates a new unique cluster/volume/databases on every invocation.
Its retained resource prefix/label remains m201a-isolated; no old resource is reused.
"""
from pathlib import Path
import json
import subprocess
import verify_m201a_isolated as isolated


if __name__ == '__main__':
    # Fail before the old read-only fingerprint if Docker/ENV-00 is unavailable.
    probe = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=20)
    if probe.returncode:
        raise RuntimeError('Docker engine must be running before isolated regression')
    info = json.loads(isolated.docker('inspect', 'easytravel-env00-postgres'))[0]
    if not info['State']['Running'] or info['Config']['Labels'].get('com.easytravel.scope') != 'env00':
        raise RuntimeError('Expected read-only ENV-00 fingerprint target unavailable')
    isolated.OUT = Path(__file__).resolve().parents[1] / 'Docs/M2-01c'
    isolated.main()
