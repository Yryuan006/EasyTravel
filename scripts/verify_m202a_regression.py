"""Run exactly the accepted 454 tests through the accepted fresh-cluster gate.

F02 + place tests have a separate network-denied runner. ENV-00 is fingerprinted
read-only; if initially stopped, it is restored to stopped in finally.
"""
import json
import os
from pathlib import Path
import subprocess
import time

import verify_m201a_isolated as isolated


def main():
    out = Path(__file__).resolve().parents[1] / "Docs/M2-02a"
    out.mkdir(exist_ok=True)
    probe = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=20)
    if probe.returncode:
        raise RuntimeError("Docker engine unavailable; no database fallback")
    info = json.loads(isolated.docker("inspect", "easytravel-env00-postgres"))[0]
    if info["Config"]["Labels"].get("com.easytravel.scope") != "env00":
        raise RuntimeError("Unexpected fingerprint target")
    originally_running = info["State"]["Running"]
    previous_options = os.environ.get("PYTEST_ADDOPTS")
    try:
        if not originally_running:
            isolated.docker("start", "easytravel-env00-postgres")
        for _ in range(100):
            ready = subprocess.run(["docker", "exec", "easytravel-env00-postgres", "pg_isready",
                                    "-U", "easytravel_env00_admin", "-d", "postgres"], capture_output=True, timeout=5)
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Read-only fingerprint target not ready; no fallback")
        # Accepted driver excludes tests/places; exclude only the new pure F02 suite.
        os.environ["PYTEST_ADDOPTS"] = "--ignore=tests/feasibility"
        isolated.OUT = out
        isolated.main()
    finally:
        if previous_options is None:
            os.environ.pop("PYTEST_ADDOPTS", None)
        else:
            os.environ["PYTEST_ADDOPTS"] = previous_options
        if not originally_running:
            isolated.docker("stop", "easytravel-env00-postgres")
        after = json.loads(isolated.docker("inspect", "easytravel-env00-postgres"))[0]
        report = dict(env00InitiallyRunning=originally_running, env00FinallyRunning=after["State"]["Running"],
                      env00ReadOnlyFingerprint=True, env00StateRestored=after["State"]["Running"] == originally_running,
                      acceptedRunner="scripts/verify_m201a_isolated.py", newClusterEveryRun=True,
                      inheritedResourcePrefix="m201a-isolated", reusedOldDatabaseForWrites=False,
                      suite="454 accepted tests; places and F02 tested separately under deny guards")
        (out / "回归环境说明.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
