"""Export and revalidate F05 examples/schemas, protect all accepted baseline files."""
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
from easytravel.backend.application.hotel_validation import OfflineHotelValidator
from easytravel.backend.domain import hotel_feasibility as domain

BASELINE = "40ce99c3aec1e107e9bee8758e13fb1aa431e654"
OUT = ROOT / "Docs/M2-02d"
MODULES = ("easytravel/backend/domain/hotel_feasibility.py", "easytravel/backend/application/hotel_validation.py")
CONTRACTS = ("HotelSource", "LuggageStoragePolicy", "CheckInPolicy", "CheckOutPolicy", "RoomCapacity",
             "HotelPolicyFacts", "HotelStay", "F05ValidationInput", "StayNightCoverage", "F05FeasibilityCheck", "F05ValidationReport")


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
        print("PASS all baseline files unchanged under Git attributes; no production F05 imports/wiring")
        allowed = {"datetime", "typing", "pydantic", "collections", "hashlib", "json", "easytravel", "places", "opening_feasibility"}
        for name in MODULES:
            for n in ast.walk(ast.parse((ROOT / name).read_text(encoding="utf-8"))):
                if isinstance(n, ast.ImportFrom): assert n.module.split(".")[0] in allowed
                if isinstance(n, ast.Import): assert all(a.name.split(".")[0] in allowed for a in n.names)
        print("PASS F05 runtime has no I/O imports, inventory/price/provider/production or F03/F04 calculator dependencies")
        fixture_path = ROOT / "tests/feasibility/fixtures/hotel_fabricated.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        validator = OfflineHotelValidator(clock=lambda: datetime.fromisoformat(fixture["clock"]))
        examples = []

        def case(name, data, expected):
            request = domain.F05ValidationInput.model_validate_json(json.dumps(data))
            report = validator.evaluate(request)
            assert report.status == expected, (name, report.status)
            assert validator.evaluate(request).model_dump_json() == report.model_dump_json()
            examples.append(dict(case_id=name, expected_status=expected, input=request.model_dump(mode="json"), report=report.model_dump(mode="json")))

        base = fixture["request"]
        case("two_nights_two_guests_exact_boundaries", base, "PASS")
        early = deepcopy(base)
        early["stays"][0].update(arrival_at="2026-12-07T14:59:59+08:00", check_in_at="2026-12-07T14:59:59+08:00")
        case("early_check_in_without_permission", early, "FAIL")
        early["facts"][0]["check_in"]["early_from"] = "13:00"
        case("explicit_synthetic_early_permission", early, "PASS")
        late = deepcopy(base)
        late["stays"][0]["check_out_at"] = "2026-12-09T10:00:01+08:00"
        case("late_check_out", late, "FAIL")
        arrival = deepcopy(base)
        arrival["stays"][0].update(arrival_at="2026-12-07T11:00:00+08:00", pre_check_in_plan="luggage_storage")
        case("early_arrival_storage_unknown", arrival, "UNKNOWN")
        arrival["facts"][0]["check_in"]["luggage"] = dict(state="ALLOWED", start="09:00", end="18:00")
        case("arrival_distinct_from_check_in_with_storage", arrival, "PASS")
        midnight = deepcopy(base)
        midnight["stays"][0].update(arrival_at="2026-12-08T00:30:00+08:00", check_in_at="2026-12-08T00:30:00+08:00")
        case("midnight_check_in_not_implicitly_permitted", midnight, "FAIL")
        midnight["facts"][0]["check_in"].update(latest="01:00", latest_day_offset=1)
        case("explicit_midnight_window", midnight, "PASS")
        capacity = deepcopy(base)
        capacity["facts"][0]["capacity"]["max_guests_per_room"] = None
        case("capacity_unknown", capacity, "UNKNOWN")
        missing = deepcopy(base)
        missing["facts"] = []
        case("policy_missing", missing, "UNKNOWN")
        expired = deepcopy(base)
        expired["facts"][0]["source"]["expires_at"] = fixture["clock"]
        case("policy_expired", expired, "UNKNOWN")
        duplicate = deepcopy(base)
        other = deepcopy(duplicate["stays"][0]); other["stay_id"] = "duplicate_stay"
        duplicate["stays"].append(other)
        case("duplicate_nights_not_double_counted", duplicate, "FAIL")
        no_stay = deepcopy(base)
        no_stay["stays"] = []
        case("required_nights_missing", no_stay, "FAIL")
        no_stay.update(trip_end_date=no_stay["trip_start_date"], facts=[], resolved_hotels=[])
        case("one_day_zero_stays", no_stay, "NOT_APPLICABLE")
        conflict = deepcopy(base)
        other = deepcopy(conflict["facts"][0]); other["fact_id"] = "conflicting_policy"
        other["check_in"]["earliest"] = "16:00"
        conflict["facts"].append(other)
        case("unresolved_check_in_sources", conflict, "UNKNOWN")
        mixed = deepcopy(late)
        mixed["facts"][0]["capacity"] = None
        case("independent_failure_and_unknown", mixed, "FAIL")
        incomplete = deepcopy(late)
        incomplete["facts"][0]["date_policies_complete"] = False
        case("incomplete_policies_cannot_prove_late_failure", incomplete, "UNKNOWN")

        schemas = {name: getattr(domain, name).model_json_schema() for name in CONTRACTS}
        for schema in schemas.values():
            assert schema["additionalProperties"] is False
            assert all(s.get("additionalProperties") is False for s in schema.get("$defs", {}).values() if s.get("type") == "object")
        write("F05契约Schema.json", schemas)
        write("ValidationReport示例.json", dict(fixture_kind=fixture["fixture_kind"], clock=fixture["clock"], cases=examples))
        exported = json.loads((OUT / "F05契约Schema.json").read_text(encoding="utf-8"))
        assert exported == {name: getattr(domain, name).model_json_schema() for name in CONTRACTS}
        saved = json.loads((OUT / "ValidationReport示例.json").read_text(encoding="utf-8"))
        for example in saved["cases"]:
            typed = domain.F05ValidationInput.model_validate_json(json.dumps(example["input"]))
            report = domain.F05ValidationReport.model_validate_json(json.dumps(example["report"]))
            assert validator.evaluate(typed) == report
            for model, value in (("F05ValidationReport", example["report"]), *(("F05FeasibilityCheck", c) for c in example["report"]["checks"])):
                for key, field in exported[model]["properties"].items():
                    if "const" in field: assert value[key] == field["const"]
        print(f"PASS {len(CONTRACTS)} strict schemas and {len(examples)} deterministic exported input/report examples, round-trips and constants")
        migrations = {}
        for p in (ROOT / "easytravel/backend/infrastructure/migrations").glob("*.sql"):
            assert p.read_bytes() == git("show", BASELINE + ":" + p.relative_to(ROOT).as_posix())
            migrations[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
        assert len(migrations) == 7
        hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (*MODULES, fixture_path.relative_to(ROOT).as_posix())}
        write("基线与边界核查.json", dict(baseline=BASELINE, baselineFilesUnchangedUnderGitAttributes=True,
            acceptedFileCount=len(accepted), noProductionF05Wiring=True, noRuntimeIOImports=True,
            migrationSHA256=migrations, artifactSHA256=hashes, ruleVersion=domain.RULE_VERSION,
            strictSchemas=len(schemas), deterministicExamples=len(examples),
            schemaConsistencyMethod="Pydantic schema regeneration, exported input/report revalidation and constant comparison; no external JSON Schema engine",
            backend454Rerun=False, databaseAccessThisRun=False,
            reusedRegressionEvidence="Docs/M2-02a/后端454项回归.txt", reusedDatabaseIsolation="Docs/M2-02a/数据库隔离.json",
            scope="offline synthetic hotel events, room capacity and booked night coverage only; stop for Review"))
        print("PASS seven migrations byte-identical; all M1/PostgreSQL/auth/budget/snapshots and F02/F03/F04 files untouched")
        print("454 database regressions NOT rerun: accepted evidence reused; no database or real supplier API access")
    print((OUT / "契约核查原始日志.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
