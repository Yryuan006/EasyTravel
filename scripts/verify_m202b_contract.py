"""Export strict F03 artifacts; assert accepted baseline has no file modifications."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from easytravel.backend.application.travel_validation import OfflineTravelValidator
from easytravel.backend.domain import travel_feasibility as domain

BASELINE = "b68531b417570d257ec3da5be97f95be9e295b36"
OUT = ROOT / "Docs/M2-02b"
MODULES = ("easytravel/backend/domain/travel_feasibility.py", "easytravel/backend/application/travel_validation.py")


def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=ROOT)


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "契约核查原始日志.txt").open("w", encoding="utf-8") as log, redirect_stdout(log):
        accepted = set(git("ls-tree", "-r", "--name-only", BASELINE).decode().splitlines())
        changed = set(git("diff", "--name-only", BASELINE).decode().splitlines())
        assert not changed & accepted, "accepted baseline modified"
        # Git's comparison honors the accepted checkout line-ending attributes.
        assert all((ROOT / name).is_file() for name in accepted)
        print("PASS every baseline file unchanged under Git attributes, including formal M1, M2-01a/c, F02 FIX-01, snapshots, budget, auth and Spike")
        stems = {Path(m).stem for m in MODULES}
        for name in accepted:
            if name.startswith("easytravel/") and name.endswith(".py"):
                for n in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                    if isinstance(n, ast.ImportFrom): assert (n.module or "").split(".")[-1] not in stems
                    if isinstance(n, ast.Import): assert not any(a.name.split(".")[-1] in stems for a in n.names)
        allowed = {"datetime", "typing", "pydantic", "collections", "hashlib", "json", "easytravel", "places", "opening_feasibility"}
        for name in MODULES:
            for n in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                if isinstance(n, ast.ImportFrom): assert n.module.split(".")[0] in allowed
                if isinstance(n, ast.Import): assert all(a.name.split(".")[0] in allowed for a in n.names)
        print("PASS no production import/wiring; new runtime imports only pure model/arithmetic libraries")
        fixture_path = ROOT / "tests/feasibility/fixtures/travel_fabricated.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        validator = OfflineTravelValidator(clock=lambda: datetime.fromisoformat(fixture["clock"]))
        cases = []

        def case(name, data, expected):
            request = domain.F03ValidationInput.model_validate_json(json.dumps(data))
            report = validator.evaluate(request)
            assert report.status == expected
            assert validator.evaluate(request) == report
            cases.append(dict(case_id=name, input=request.model_dump(mode="json"), report=report.model_dump(mode="json")))

        data = fixture["request"]
        case("sufficient", data, "PASS")
        exact = deepcopy(data)
        exact["pairs"][0]["next"]["start"] = "2026-12-07T12:45:00+08:00"
        case("exact_equality", exact, "PASS")
        fail = deepcopy(data)
        fail["pairs"][0]["next"]["start"] = "2026-12-07T12:30:00+08:00"
        case("insufficient", fail, "FAIL")
        missing = deepcopy(data)
        missing["facts"] = []
        case("missing", missing, "UNKNOWN")
        conflict = deepcopy(data)
        other = deepcopy(conflict["facts"][0])
        other["fact_id"] = "fact_b"
        other["duration"]["riding_seconds"] += 60
        conflict["facts"].append(other)
        case("conflict", conflict, "UNKNOWN")
        mixed = deepcopy(fail)
        pair = deepcopy(mixed["pairs"][0])
        pair.update(pair_id="unknown_pair", leg_id="unknown_leg")
        mixed["pairs"].append(pair)
        case("independent_fail_and_unknown", mixed, "FAIL")
        schemas = {name: getattr(domain, name).model_json_schema() for name in (
            "LegEndpoint", "LegDuration", "LegApplicableScope", "TravelLegSource", "TravelActivity",
            "TravelLegFacts", "ConsecutiveActivityPair", "F03FeasibilityCheck", "F03ValidationInput", "F03ValidationReport")}
        for schema in schemas.values():
            assert schema["additionalProperties"] is False
            assert all(s.get("additionalProperties") is False for s in schema.get("$defs", {}).values() if s.get("type") == "object")
        write("F03契约Schema.json", schemas)
        write("ValidationReport示例.json", dict(clock=fixture["clock"], fixture_kind=fixture["fixture_kind"], cases=cases))
        migrations = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "easytravel/backend/infrastructure/migrations").glob("*.sql")}
        assert len(migrations) == 7
        for p in (ROOT / "easytravel/backend/infrastructure/migrations").glob("*.sql"):
            assert p.read_bytes() == git("show", BASELINE + ":" + p.relative_to(ROOT).as_posix())
        write("基线与契约核查.json", dict(baseline=BASELINE, allBaselineFilesUnchangedUnderGitAttributes=True,
            acceptedFileCount=len(accepted), noProductionWiring=True, noRuntimeIOImports=True,
            migrationSHA256=migrations, fixtureSHA256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            ruleVersion=domain.RULE_VERSION, bufferVersion=domain.BUFFER_VERSION,
            backend454Rerun=False, databaseAccessThisRun=False,
            reusedRegressionEvidence="Docs/M2-02a/后端454项回归.txt",
            reusedDatabaseIsolation="Docs/M2-02a/数据库隔离.json",
            scope="offline synthetic F03 only; F04 not evaluated; stop for architecture Review"))
        print("PASS 10 strict schemas, 6 deterministic input/report examples; F03 never grants VERIFIED, READY or F04 PASS")
        print("454 database regression NOT rerun; reused accepted evidence; no database access this run")
    print((OUT / "契约核查原始日志.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
