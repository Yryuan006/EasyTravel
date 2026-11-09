import pytest


@pytest.fixture
def payload():
    items = [dict(id="hotel", kind="hotel", resource_id="hotel-001", title="合成酒店",
                  start="2026-12-01T14:00:00+08:00", end="2026-12-03T10:00:00+08:00",
                  room_type="standard", check_in="2026-12-01", check_out="2026-12-03", rooms=1)]
    for n in range(4):
        items.append(dict(id=f"activity-{n}", kind="activity", resource_id=f"resource-{n}",
                          title="合成活动", start=f"2026-12-02T{10+n:02}:00:00+08:00",
                          end=f"2026-12-02T{11+n:02}:00:00+08:00"))
    costs = []
    for n, (item, amount) in enumerate(zip(items, [60000, 24000, 16000, 24000, 10000])):
        costs.append(dict(id=f"cost-{n}", item_id=item["id"], amount_cents=amount,
                          category="quote", unknown_reason=None, evidence_id=f"evidence-{n}",
                          is_mock=True, query=dict(resource_id=item["resource_id"],
                          start_date=item["start"][:10], end_date=item["end"][:10], currency="CNY",
                          room_type=item.get("room_type"), rooms=item.get("rooms"))))
    return dict(items=items, costs=costs)


@pytest.fixture
def requirements():
    from easytravel.contracts import TripRequirements
    return TripRequirements.model_validate_json('''{"destination":"杭州", "start_date":"2026-12-01",
        "end_date":"2026-12-03", "timezone":"Asia/Shanghai", "currency":"CNY",
        "hard_limit_cents":134000, "requirements_revision":1}''')
