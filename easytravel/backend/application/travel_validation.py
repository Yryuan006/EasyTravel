"""Internal offline F03 entry point; deliberately absent from production flows."""
from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from easytravel.backend.domain.opening_feasibility import aggregate_status
from easytravel.backend.domain.travel_feasibility import F03ValidationInput, F03ValidationReport, check_pair


class OfflineTravelValidator:
    def __init__(self, *, clock: Callable[[], datetime]):
        self._clock = clock

    def evaluate(self, request: F03ValidationInput) -> F03ValidationReport:
        request = F03ValidationInput.model_validate_json(request.model_dump_json())
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("aware evaluation clock required")
        request = F03ValidationInput(
            pairs=tuple(sorted(request.pairs, key=lambda p: p.pair_id)),
            facts=tuple(sorted(request.facts, key=lambda f: f.fact_id)),
            resolved_endpoints=tuple(sorted(request.resolved_endpoints, key=lambda e: (e.resource.internal_resource_id, e.access_id, e.role))))
        checks = tuple(c for p in request.pairs for c in check_pair(p, request.facts, request.resolved_endpoints, now))
        status = aggregate_status(tuple(c.status for c in checks))
        used = {i for c in checks for i in c.fact_ids}
        deadlines = [f.source.expires_at for f in request.facts if f.fact_id in used]
        raw = json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return F03ValidationReport(evaluated_at=now, input_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            checks=checks, status=status, valid_until=min(deadlines) if status == "PASS" and deadlines else None)
