"""Offline internal F05; deliberately absent from generation/confirmation APIs."""
from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from easytravel.backend.domain.opening_feasibility import aggregate_status
from easytravel.backend.domain.hotel_feasibility import F05ValidationInput, F05ValidationReport, check_nights, check_stay


class OfflineHotelValidator:
    def __init__(self, *, clock: Callable[[], datetime]):
        self._clock = clock

    def evaluate(self, request: F05ValidationInput) -> F05ValidationReport:
        request = F05ValidationInput.model_validate_json(request.model_dump_json())
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("aware clock required")
        data = request.model_dump(mode="json")
        for key, field in (("stays", "stay_id"), ("facts", "fact_id"), ("resolved_hotels", "internal_resource_id")):
            data[key].sort(key=lambda item: item[field])
        request = F05ValidationInput.model_validate_json(json.dumps(data))
        coverage, night_checks = check_nights(request)
        checks = tuple(c for s in request.stays for c in check_stay(s, request, now)) + night_checks
        status = aggregate_status(tuple(c.status for c in checks))
        used = {i for c in checks for i in c.fact_ids}
        deadlines = [f.source.expires_at for f in request.facts if f.fact_id in used]
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return F05ValidationReport(evaluated_at=now, input_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            checks=checks, night_coverage=coverage, status=status, valid_until=min(deadlines) if status == "PASS" and deadlines else None)
