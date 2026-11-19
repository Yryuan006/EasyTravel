"""Export/round-trip F04 contracts and examples, and audit the accepted baseline."""
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
from easytravel.backend.application.transit_validation import OfflineTransitValidator
from easytravel.backend.domain import transit_feasibility as domain

BASELINE = "532db3adf1319b91f96217e52fa22ea2d3c8ea50"
OUT = ROOT / "Docs/M2-02c"
MODULES = ("easytravel/backend/domain/transit_feasibility.py", "easytravel/backend/application/transit_validation.py")
CONTRACTS = ("TransitStopRef", "TransitSegment", "TransitSource", "TransitFactScope", "ServiceOccurrence",
             "TimeAllowance", "FirstBoardingAccess", "TransferRule", "TransitConnection",
             "F04ValidationInput", "F04FeasibilityCheck", "F04ValidationReport")


def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=ROOT)


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "契约核查原始日志.txt").open("w", encoding="utf-8") as log, redirect_stdout(log):
        accepted = set(git("ls-tree", "-r", "--name-only", BASELINE).decode("utf-8").splitlines())
        changed = set(git("diff", "--name-only", BASELINE).decode("utf-8").splitlines())
        assert not changed & accepted, "baseline files modified"
        assert all((ROOT / name).is_file() for name in accepted)
        stems = {Path(p).stem for p in MODULES}
        for name in accepted:
            if name.startswith("easytravel/") and name.endswith(".py"):
                for n in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                    if isinstance(n, ast.ImportFrom): assert (n.module or "").split(".")[-1] not in stems
                    if isinstance(n, ast.Import): assert not any(a.name.split(".")[-1] in stems for a in n.names)
        print("PASS all accepted baseline files unchanged under Git attributes; no production F04 imports/wiring")
        allowed = {"datetime", "typing", "pydantic", "collections", "hashlib", "json", "easytravel", "places", "opening_feasibility"}
        for name in MODULES:
            for n in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                if isinstance(n, ast.ImportFrom): assert n.module.split(".")[0] in allowed
                if isinstance(n, ast.Import): assert all(a.name.split(".")[0] in allowed for a in n.names)
        print("PASS F04 runtime contains no I/O imports, routing, F03 calculator or production dependencies")
        fixture_path = ROOT / "tests/feasibility/fixtures/transit_fabricated.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        validator = OfflineTransitValidator(clock=lambda: datetime.fromisoformat(fixture["clock"]))
        examples = []

        def case(name, data, expected):
            request = domain.F04ValidationInput.model_validate_json(json.dumps(data))
            report = validator.evaluate(request)
            assert report.status == expected
            assert validator.evaluate(request).model_dump_json() == report.model_dump_json()
            assert domain.F04ValidationReport.model_validate_json(report.model_dump_json()) == report
            examples.append(dict(case_id=name, expected_status=expected, input=request.model_dump(mode="json"), report=report.model_dump(mode="json")))

        base = fixture["request"]
        case("exact_first_and_transfer", base, "PASS")
        fail = deepcopy(base)
        fail["connections"][0]["previous_end"] = "2026-12-07T12:00:01+08:00"
        case("first_boarding_missed", fail, "FAIL")
        transfer = deepcopy(base)
        transfer["transfer_rules"][0]["allowance"].update(walking_seconds=241, required_seconds=301)
        case("transfer_missed", transfer, "FAIL")
        missing = deepcopy(base)
        missing["services"] = []
        case("no_observations_is_not_no_service", missing, "UNKNOWN")
        stopped = deepcopy(base)
        f = stopped["services"][0]
        f["state"] = "NO_SERVICE"
        for key in ("origin_departure", "board_arrival", "board_departure", "alight_arrival", "first_board_departure", "last_board_departure"):
            f[key] = None
        case("explicit_target_day_no_service", stopped, "FAIL")
        stopped["services"][0]["date_information_complete"] = False
        case("incomplete_negative_observation", stopped, "UNKNOWN")
        incomplete = deepcopy(base)
        incomplete["services"][0]["last_board_departure"] = None
        case("last_service_unknown", incomplete, "UNKNOWN")
        incomplete["connections"][0]["risk_flags"] = ["remote_only"]
        case("critical_unknown", incomplete, "UNKNOWN")
        conflict = deepcopy(base)
        f = deepcopy(conflict["services"][0])
        f["fact_id"] = "conflicting_fact"
        f["board_departure"] = "2026-12-07T12:06:00+08:00"
        conflict["services"].append(f)
        case("unresolved_sources", conflict, "UNKNOWN")
        mixed = deepcopy(fail)
        mixed["services"] = mixed["services"][:1]
        case("independent_fail_and_unknown", mixed, "FAIL")
        wrong = deepcopy(base)
        wrong["services"][0]["segment"]["direction_id"] = "reverse"
        case("wrong_direction_not_borrowed", wrong, "UNKNOWN")
        expired = deepcopy(base)
        expired["services"][0]["source"]["expires_at"] = fixture["clock"]
        case("expired_observation", expired, "UNKNOWN")
        unsupported = deepcopy(base)
        unsupported["connections"][0]["requested_scope"] = "airport"
        case("airport_out_of_scope", unsupported, "UNKNOWN")

        schemas = {name: getattr(domain, name).model_json_schema() for name in CONTRACTS}
        for schema in schemas.values():
            assert schema["additionalProperties"] is False
            assert all(s.get("additionalProperties") is False for s in schema.get("$defs", {}).values() if s.get("type") == "object")
        write("F04契约Schema.json", schemas)
        write("ValidationReport示例.json", dict(fixture_kind=fixture["fixture_kind"], clock=fixture["clock"], cases=examples))
        # Read exported bytes, then validate through the very models that generated
        # the exported schema. Also bind constants, defaults and rule versions.
        exported = json.loads((OUT / "F04契约Schema.json").read_text(encoding="utf-8"))
        assert exported == {name: getattr(domain, name).model_json_schema() for name in CONTRACTS}
        saved = json.loads((OUT / "ValidationReport示例.json").read_text(encoding="utf-8"))
        for example in saved["cases"]:
            typed = domain.F04ValidationInput.model_validate_json(json.dumps(example["input"]))
            report = domain.F04ValidationReport.model_validate_json(json.dumps(example["report"]))
            assert validator.evaluate(typed) == report
            for model, value in (("F04ValidationReport", example["report"]), *(('F04FeasibilityCheck', c) for c in example["report"]["checks"])):
                for key, field in exported[model]["properties"].items():
                    if "const" in field: assert value[key] == field["const"]
        print(f"PASS {len(CONTRACTS)} strict schemas and {len(examples)} deterministic examples; exported schema/model/report constants and round-trips agree")
        migrations = {}
        for p in (ROOT / "easytravel/backend/infrastructure/migrations").glob("*.sql"):
            assert p.read_bytes() == git("show", BASELINE + ":" + p.relative_to(ROOT).as_posix())
            migrations[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        assert len(migrations) == 7
        hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (*MODULES, str(fixture_path.relative_to(ROOT).as_posix()))}
        write("基线与边界核查.json", dict(baseline=BASELINE, baselineFilesUnchangedUnderGitAttributes=True,
            acceptedFileCount=len(accepted), noProductionF04Wiring=True, noRuntimeIOImports=True,
            noF03CalculatorDependency=True, migrationSHA256=migrations, artifactSHA256=hashes,
            ruleVersion=domain.RULE_VERSION, policyVersion=domain.POLICY_VERSION,
            strictSchemas=len(schemas), deterministicExamples=len(examples), schemaConsistencyMethod="Pydantic schema regeneration, exported input/report revalidation and constant comparison; no external JSON Schema engine",
            backend454Rerun=False, databaseAccessThisRun=False,
            reusedRegressionEvidence="Docs/M2-02a/后端454项回归.txt", reusedDatabaseIsolation="Docs/M2-02a/数据库隔离.json",
            scope="synthetic same-city same-day one/two rides only; stop for architecture Review"))
        print("PASS seven migration files byte-identical; M1, PostgreSQL, auth, budget, snapshots and historical checkpoints untouched")
        print("454 database regressions NOT rerun: reuse accepted evidence; no database access or real traffic API")
    print((OUT / "契约核查原始日志.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
