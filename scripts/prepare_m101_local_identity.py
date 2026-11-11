"""Generate local-only credentials into a new ignored file; never print tokens."""
import argparse
import json
from pathlib import Path
import re
import secrets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--actors", nargs="+", default=["local_owner"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.env_file).resolve()
    if output.parent != root / ".local" or output.exists():
        raise SystemExit("Use a new file directly inside .local; credentials are never overwritten")
    if len(set(args.actors)) != len(args.actors) or not all(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", a) for a in args.actors):
        raise SystemExit("Use unique valid local actor identifiers")
    identities = [{"actor": actor, "token": secrets.token_urlsafe(32)} for actor in args.actors]
    output.parent.mkdir(exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write("EASYTRAVEL_APP_ENVIRONMENT=local\nEASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED=true\n")
        stream.write("EASYTRAVEL_APP_LOCAL_IDENTITIES='" + json.dumps(identities, separators=(",", ":")) + "'\n")
    print(f"Created local-only credential file={output.relative_to(root)} actors={','.join(args.actors)}")
    print("Tokens were not printed. This is not production authentication. Keep the file private.")


if __name__ == "__main__":
    main()
