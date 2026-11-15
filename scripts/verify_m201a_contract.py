"""Offline audit: unchanged accepted files/API, new strict place-only schemas."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

from easytravel.backend.application.place_lookup import PlaceObservation
from easytravel.backend.config import Settings
from easytravel.backend.domain.places import (
    PlaceCandidate, PlaceFacts, PlaceQuery, ResourceRef, SourceResult,
)
from easytravel.backend.main import create_app

BASELINE = "4c86b80fb0970120865b5f347bb08d7e084b1305"
OUTPUT = Path("Docs/M2-01a")
MODULES = (
    "easytravel/backend/domain/places.py",
    "easytravel/backend/application/place_sources.py",
    "easytravel/backend/application/place_lookup.py",
    "easytravel/backend/infrastructure/synthetic_places.py",
)


def git(*args):
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], capture_output=True, check=True).stdout


def main():
    accepted_paths = set(git("ls-tree", "-r", "--name-only", BASELINE).decode("utf-8").splitlines())
    changed = set(git("diff", "--name-only", BASELINE).decode("utf-8").splitlines())
    assert not (changed & accepted_paths), "Accepted tracked files were changed"
    schema = create_app(Settings(environment="test")).openapi()
    accepted = json.loads(git("show", BASELINE + ":Docs/M1-08_OpenAPI.json"))
    assert schema == accepted, "Formal API changed"
    migrations = {}
    for path in Path("easytravel/backend/infrastructure/migrations").glob("*.sql"):
        assert path.read_bytes() == git("show", BASELINE + ":" + path.as_posix())
        migrations[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(migrations) == 7
    # No registration/import into any accepted production composition or business module.
    for name in accepted_paths:
        if name.startswith("easytravel/") and name.endswith(".py"):
            tree = ast.parse(Path(name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[-1] not in {"places", "place_lookup", "place_sources", "synthetic_places"}
    schemas = {cls.__name__: cls.model_json_schema() for cls in (
        PlaceQuery, ResourceRef, PlaceCandidate, PlaceFacts, SourceResult[PlaceCandidate], PlaceObservation)}
    assert all(s["additionalProperties"] is False for s in schemas.values())
    report = dict(baseline=BASELINE, acceptedFilesUnchanged=True, actualOpenAPIUnchanged=True,
                  migrationFilesUnchanged=migrations, noNewMigration=True,
                  notWiredIntoBusinessPaths=True, newRuntimeFiles=list(MODULES),
                  syntheticFixtureSHA256=hashlib.sha256(Path("easytravel/backend/infrastructure/synthetic_places.json").read_bytes()).hexdigest(),
                  placeEvidenceUsableForRealTrip=False, providerRecordings=False,
                  note="Static/API/schema checks plus separate network-denied source tests; not real-data validation.")
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "契约与基线核查.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUTPUT / "地点契约Schema.json").write_text(json.dumps(schemas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PASS accepted tracked files, formal OpenAPI, 001-007 migration bytes and unwired place-only schemas")


if __name__ == "__main__":
    main()
