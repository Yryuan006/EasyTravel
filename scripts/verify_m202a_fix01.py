"""Reuse accepted offline guards/contract exports; preserve original phase evidence."""
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Docs/M2-02a/FIX-01"
BASELINE = "d656c01f5713d48c3b7662dfadad2eb3db6582c5"
ALLOWED_EXISTING = {
    "easytravel/backend/domain/opening_feasibility.py",
    "tests/feasibility/test_opening_feasibility.py",
    "Docs/M2-02a/README.md",
    "Docs/M2-02a/实施与验收报告.md",
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("offline", "before", "contract"):
        raise SystemExit("usage: verify_m202a_fix01.py offline|before|contract")
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] in ("offline", "before"):
        # Do not import pytest or application modules before installing deny guards.
        import verify_m202a_offline as runner
        runner.OUT = OUT if sys.argv[1] == "offline" else OUT / "修复前反例"
        runner.main()
    else:
        sys.path.insert(0, str(ROOT))
        import verify_m202a_contract as contract
        changed = set(contract.git("diff", "--name-only", BASELINE).decode("utf-8").splitlines())
        accepted = set(contract.git("ls-tree", "-r", "--name-only", BASELINE).decode("utf-8").splitlines())
        assert changed & accepted <= ALLOWED_EXISTING, "FIX-01 exceeded existing-file scope"
        contract.OUT = OUT
        with (OUT / "契约核查原始日志.txt").open("w", encoding="utf-8") as log, redirect_stdout(log):
            contract.main()
            print("PASS FIX-01 only changes F02 rule/tests/notes; original business, database, place and evidence files unchanged")
        schemas = json.loads((OUT / "F02契约Schema.json").read_text(encoding="utf-8"))
        from easytravel.backend.domain.opening_feasibility import RULE_VERSION
        assert RULE_VERSION == "F02-synthetic-v1-fix01"
        for name in ("FeasibilityCheck", "ValidationReport"):
            assert schemas[name]["properties"]["rule_version"]["const"] == RULE_VERSION
        assert "target-date OVERRIDE" in schemas["OpeningFacts"]["properties"]["exceptions_complete"]["description"]
        report = dict(baseline=BASELINE, changedExistingFiles=sorted(changed & accepted),
                      onlyF02RuntimeChanged=True, ruleVersion=RULE_VERSION,
                      originalBusinessDatabasePlaceAndEvidenceUnchanged=True,
                      backend454Rerun=False, reason="No production business/database code changes or F02 production wiring",
                      reusedRegressionEvidence="Docs/M2-02a/后端454项回归.txt", databaseAccessThisRun=False)
        (OUT / "FIX-01范围核查.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print((OUT / "契约核查原始日志.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
