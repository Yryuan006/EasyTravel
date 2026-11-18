"""Offline-only F02 entry point; not wired into generation or confirmation."""
from collections.abc import Callable
from datetime import datetime
import hashlib
import json

from easytravel.backend.domain.opening_feasibility import (
    OpeningValidationInput, ValidationReport, aggregate_status, check_visit,
)


class OfflineOpeningValidator:
    def __init__(self, *, clock: Callable[[], datetime]):
        self._clock = clock

    def evaluate(self, request: OpeningValidationInput) -> ValidationReport:
        # Rebuild nested schemas, including model_copy/model_construct bypasses.
        request = OpeningValidationInput.model_validate_json(request.model_dump_json())
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("aware evaluation clock required")
        # Canonical order prevents input collection ordering from changing a report.
        visits = tuple(sorted(request.visits, key=lambda v: v.item_id))
        facts = tuple(sorted(request.facts, key=lambda f: f.fact_id))
        normalized = OpeningValidationInput(visits=visits, facts=facts)
        raw = json.dumps(normalized.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        checks = tuple(c for v in visits for c in check_visit(v, facts, now))
        status = aggregate_status(tuple(c.status for c in checks))
        used_ids = {i for c in checks for i in c.fact_ids}
        deadlines = [f.source.expires_at for f in facts if f.fact_id in used_ids]
        # Lifetime is an offline observation TTL, never a promise about travel dates.
        valid_until = min(deadlines) if status == "PASS" and deadlines and min(deadlines) > now else None
        return ValidationReport(evaluated_at=now, input_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                                checks=checks, status=status, valid_until=valid_until)
