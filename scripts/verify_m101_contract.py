"""Offline comparison of implemented creation/read contract with the M1-00 draft."""
import json
from pathlib import Path
import re
import sys

from fastapi.openapi.models import OpenAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from easytravel.backend.config import Settings
from easytravel.backend.main import create_app


def main():
    root = Path(__file__).resolve().parents[1]
    text = (root / "Docs/M1-00_API契约草案.md").read_text(encoding="utf-8")
    draft = json.loads(re.search(r"```json\n(.*?)\n```", text, re.S)[1])
    actual = create_app(Settings(environment="test")).openapi()
    OpenAPI.model_validate(actual)
    schemas = actual["components"]["schemas"]
    expected = draft["components"]["schemas"]
    assert set(actual["paths"]) == {"/api/v1/health", "/api/v1/trips", "/api/v1/trips/{trip_id}"}
    assert set(schemas["RequirementsInput"]["required"]) == set(expected["RequirementsInput"]["required"])
    assert set(schemas["RequirementsInput"]["properties"]) == set(expected["RequirementsInput"]["properties"])
    assert schemas["RequirementsInput"]["additionalProperties"] is False
    assert set(schemas["CreateTripRequest"]["required"]) == {"requirements"}
    assert set(schemas["TripView"]["properties"]) == set(expected["TripView"]["properties"])
    assert set(schemas["IntakeView"]["properties"]) == set(expected["IntakeView"]["properties"])
    assert set(schemas["TripView"]["required"]) == set(expected["TripView"]["required"])
    for path,method,opid,success in [("/api/v1/trips","post","createTrip","201"),
                                    ("/api/v1/trips/{trip_id}","get","getTrip","200")]:
        operation=actual["paths"][path][method]
        assert operation["operationId"]==opid
        assert operation["security"]==[{"LocalDevBearer":[]}]
        assert operation["responses"][success]["content"]["application/json"]["schema"]=={"$ref":"#/components/schemas/TripView"}
        assert all(str(code) in operation["responses"] for code in [401,403,404,409,422,500,503])
    param=actual["paths"]["/api/v1/trips"]["post"]["parameters"]
    assert any(p["name"]=="Idempotency-Key" and p["required"] and p["schema"]["format"]=="uuid" for p in param)
    assert actual["paths"]["/api/v1/trips"]["post"]["requestBody"]["content"]["application/json"]["schema"]=={"$ref":"#/components/schemas/CreateTripRequest"}
    # Traverse all schema references, including generic discriminated Answer variants.
    def refs(value):
        if isinstance(value,dict):
            if "$ref" in value:
                target=actual
                for segment in value["$ref"][2:].split("/"):
                    target=target[segment.replace("~1","/").replace("~0","~")]
            for child in value.values(): refs(child)
        elif isinstance(value,list):
            for child in value: refs(child)
    refs(actual)
    output=root/"Docs/M1-01_openapi.json"
    output.write_text(json.dumps(actual,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PASS: OpenAPI 3.1 FastAPI model validation; all references resolve")
    print("PASS: only health + POST trips + GET trip; operation IDs, statuses, bearer security and UUID header match")
    print("PASS: all required input fields, no extra fields; TripView and IntakeView fields/required sets match M1-00")
    print("SCOPE: TripView narrows working/latest_confirmed_version_id/active_run_id to null; synchronization=NO_DRAFT, plan_confirmation=UNCONFIRMED")
    print("SCOPE: Answer uses named discriminated schemas; date fields use explicit ISO-date parsing without numeric coercion")
    print("SEMANTICS: destination/raw_text minimum, nonblank text, date order and strict input are validated by Pydantic; see HTTP tests")
    print("LIMIT: not a full JSON-Schema equivalence proof; no 12 deferred business operations are implemented")
    print("Saved Docs/M1-01_openapi.json")


if __name__ == "__main__":
    main()
