"""Offline internal F04 entry. Not wired into any business endpoint."""
from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from easytravel.backend.domain.opening_feasibility import aggregate_status
from easytravel.backend.domain.transit_feasibility import F04ValidationInput, F04ValidationReport, check_connection


class OfflineTransitValidator:
    def __init__(self, *, clock: Callable[[], datetime]):
        self._clock = clock

    def evaluate(self, request: F04ValidationInput) -> F04ValidationReport:
        request = F04ValidationInput.model_validate_json(request.model_dump_json())
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("aware clock required")
        data = request.model_dump(mode="json")
        data["resolved_stops"].sort(key=lambda s: (s["resource"]["internal_resource_id"], s["access_id"], s["role"]))
        for key in ("services", "first_access_rules", "transfer_rules"):
            data[key].sort(key=lambda f: f["fact_id"])
        data["connections"].sort(key=lambda c: c["connection_id"])
        for c in data["connections"]: c["risk_flags"].sort()
        request = F04ValidationInput.model_validate_json(json.dumps(data))
        checks = tuple(check for c in request.connections for check in check_connection(c, request, now))
        status = aggregate_status(tuple(c.status for c in checks))
        used = {i for c in checks for i in c.fact_ids}
        deadlines = [f.source.expires_at for f in (*request.services, *request.first_access_rules, *request.transfer_rules) if f.fact_id in used]
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return F04ValidationReport(evaluated_at=now, input_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            checks=checks, status=status, valid_until=min(deadlines) if status == "PASS" and deadlines else None)
