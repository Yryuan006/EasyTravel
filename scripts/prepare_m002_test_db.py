"""Create a fresh retained database/role in the authorized ENV-00 container only.

Never drops resources, overwrites credentials, or touches existing business data.
"""
import argparse
from pathlib import Path
import secrets
import subprocess
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.env_file).resolve()
    if output.parent != root / ".local" or output.exists():
        raise SystemExit("Use a new credential file directly inside .local; existing files are preserved")
    scope = subprocess.run(["docker", "inspect", "easytravel-env00-postgres", "--format",
                            '{{index .Config.Labels "com.easytravel.scope"}}'],
                           check=True, capture_output=True, text=True).stdout.strip()
    if scope != "env00":
        raise SystemExit("Refusing unrecognized container")
    suffix = uuid4().hex[:12]
    role, database = "easytravel_m002_" + suffix, "easytravel_m002_test_" + suffix
    password = secrets.token_hex(32)
    sql = (f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;\n"
           f"CREATE DATABASE {database} OWNER {role};\n"
           f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC;\n")
    result = subprocess.run(["docker", "exec", "-i", "easytravel-env00-postgres", "psql", "-X",
                             "-v", "ON_ERROR_STOP=1", "-U", "easytravel_env00_admin", "-d", "postgres"],
                            input=sql, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("Test database preparation failed; inspect dedicated container locally")
    output.parent.mkdir(exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(f"EASYTRAVEL_DATABASE_URL=postgresql://{role}:{password}@127.0.0.1:55432/{database}\n")
    print(f"Created dedicated test database={database} owner={role}; credentials={output.relative_to(root)}")
    print("No existing databases modified or removed; new test resources retained.")


if __name__ == "__main__":
    main()
