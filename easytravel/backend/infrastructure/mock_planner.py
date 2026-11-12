"""Deterministic synthetic content. No suppliers, real timetables or zero-price guesses."""
import json
from datetime import timedelta
from ...model_adapter import MockAdapter, ModelReply

def mock_adapter(input):
    start, end = input.dates.value.start_date, input.dates.value.end_date
    items, costs = [], []
    def add(id, title, day, hour, *, hotel=False):
        last = end if hotel else day
        item = dict(id=id,resource_id='mock_'+id,kind='hotel' if hotel else 'activity',title=title,
            start=f'{day}T{hour}:00:00+08:00',end=f'{last}T'+('10' if hotel else str(int(hour)+1).zfill(2))+':00:00+08:00')
        if hotel:
            item.update(room_type='模拟建议：一间双人房，待核验',rooms=1,check_in=str(start),check_out=str(end))
        items.append(item)
        costs.append(dict(id='cost_'+id,item_id=id,amount_cents=(30000*(end-start).days if hotel else None),
            category='estimate' if hotel else 'unknown',unknown_reason=None if hotel else '模拟项目；交通、餐饮、门票或杂费尚无价格依据（所有旅客合计）',
            query=dict(resource_id=item['resource_id'],start_date=str(day),end_date=str(last),currency='CNY',room_type=item.get('room_type'),rooms=item.get('rooms')),
            evidence_id=None,is_mock=True))
    for n in range((end-start).days+1):
        day = start+timedelta(days=n)
        add(f'activity_{n}','模拟：轻松活动建议，路线及营业未核验',day,'11')
        add(f'local_{n}','模拟：市内公共交通、餐饮及必要杂费待核验',day,'13')
    add('outbound','模拟：出发地至目的地公共交通时段建议，无真实班次',start,'08')
    add('return','模拟：返程公共交通时段建议，无真实班次',end,'17')
    if end>start:
        add('hotel','模拟住宿建议，非真实酒店或报价',start,'14',hotel=True)
    return MockAdapter(ModelReply(text=json.dumps(dict(items=items,costs=costs),ensure_ascii=False)))
