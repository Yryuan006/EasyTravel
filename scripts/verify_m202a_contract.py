"""Export repeatable F02 examples/schema and audit the accepted runtime boundary."""
import ast
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from easytravel.backend.application.opening_validation import OfflineOpeningValidator
from easytravel.backend.domain.opening_feasibility import (
    ActivityVisit, AdmissionRule, FeasibilityCheck, OpeningException, OpeningFacts,
    OpeningValidationInput, ValidationReport,
)
from easytravel.backend.config import Settings
from easytravel.backend.main import create_app

BASELINE = "c822490b02c9eac07456ddc3d02dc4cd4a4a8307"
OUT = ROOT / "Docs/M2-02a"
MODULES = (
    "easytravel/backend/domain/opening_feasibility.py",
    "easytravel/backend/application/opening_validation.py",
)


def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=ROOT)


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    accepted = set(git("ls-tree", "-r", "--name-only", BASELINE).decode("utf-8").splitlines())
    changed = set(git("diff", "--name-only", BASELINE).decode("utf-8").splitlines())
    assert not accepted & changed, "Accepted baseline files changed"
    assert create_app(Settings(environment="test")).openapi() == json.loads(git("show", BASELINE + ":Docs/M1-08_OpenAPI.json"))
    migrations = {}
    for path in (ROOT / "easytravel/backend/infrastructure/migrations").glob("*.sql"):
        assert path.read_bytes() == git("show", BASELINE + ":" + path.relative_to(ROOT).as_posix())
        migrations[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(migrations) == 7
    stems = {Path(p).stem for p in MODULES}
    for name in accepted:
        if name.startswith("easytravel/") and name.endswith(".py"):
            for node in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".")[-1] not in stems
                elif isinstance(node, ast.Import):
                    assert not any(n.name.split(".")[-1] in stems for n in node.names)
    allowed = {"datetime", "typing", "pydantic", "places", "collections", "hashlib", "json", "easytravel"}
    for name in MODULES:
        for node in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert node.module.split(".")[0] in allowed
            elif isinstance(node, ast.Import):
                assert all(n.name.split(".")[0] in allowed for n in node.names)
    fixture_path = ROOT / "tests/feasibility/fixtures/opening_fabricated.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    now = datetime.fromisoformat(fixture["clock"])
    validator = OfflineOpeningValidator(clock=lambda: now)
    scenarios = []

    def case(name, request, expected):
        typed = OpeningValidationInput.model_validate_json(json.dumps(request, ensure_ascii=False))
        report = validator.evaluate(typed)
        assert report.status == expected
        assert validator.evaluate(typed).model_dump_json() == report.model_dump_json()
        scenarios.append(dict(case_id=name, expected_status=expected, input=typed.model_dump(mode="json"), report=report.model_dump(mode="json")))

    base = fixture["request"]
    case("S01_fully_contained", base, "PASS")
    lunch = deepcopy(base)
    lunch["facts"][0]["weekly"][0]["schedule"]["windows"] = [dict(start="08:00", end="12:00"), dict(start="13:00", end="18:00")]
    lunch["visits"][0].update(start="2026-12-07T11:00:00+08:00", end="2026-12-07T14:00:00+08:00")
    case("S02_lunch_gap", lunch, "FAIL")
    conflict = deepcopy(base)
    conflict["visits"][0]["important"] = True
    conflict["facts"][0]["exceptions"] = [dict(applicable_date="2026-12-07", resolution="UNRESOLVED", schedule=dict(closed=True, windows=[], admission=None))]
    case("Q06_unresolved_notice", conflict, "UNKNOWN")
    conflict["facts"][0]["exceptions"][0]["resolution"] = "OVERRIDE"
    case("Q06_resolved_closed", conflict, "FAIL")
    missing = deepcopy(base)
    missing["facts"] = []
    case("S05_missing_hours", missing, "UNKNOWN")
    mixed = deepcopy(base)
    unknown = deepcopy(base["visits"][0])
    unknown["item_id"] = "visit_unknown"
    unknown["resource"]["internal_resource_id"] = "synthetic_internal_missing"
    mixed["visits"].append(unknown)
    mixed["visits"][0].update(start="2026-12-07T16:30:00+08:00", end="2026-12-07T17:30:00+08:00")
    case("S06_fail_over_unknown", mixed, "FAIL")
    write("ValidationReport示例.json", dict(fixture_kind=fixture["fixture_kind"], clock=fixture["clock"], cases=scenarios))
    contracts = {c.__name__: c.model_json_schema() for c in (OpeningFacts, OpeningException, AdmissionRule, ActivityVisit, FeasibilityCheck, ValidationReport, OpeningValidationInput)}
    assert all(c["additionalProperties"] is False for c in contracts.values())
    write("F02契约Schema.json", contracts)
    write("基线与契约核查.json", dict(
        baseline=BASELINE, originalTrackedFilesUnchanged=True, formalOpenAPIUnchanged=True,
        migrationBytesUnchanged=migrations, noNewMigration=True, noAcceptedRuntimeImportsNewModules=True,
        newRuntimeHasNoIOImports=True, noCoordinatesOrConversion=True,
        fixtureSHA256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        deterministicExampleCount=len(scenarios), exampleStatuses={c["case_id"]: c["report"]["status"] for c in scenarios},
        implementedScope="synthetic F02 only; no real-source verification or production wiring"))
    print("PASS unchanged baseline/OpenAPI/7 migrations; no production wiring or I/O dependencies")
    print("PASS strict schemas and 6 deterministic behavioral examples, including both Q06 variants")


if __name__ == "__main__":
    main()
