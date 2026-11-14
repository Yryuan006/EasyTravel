"""Deliberately small grammar, never a natural-language guessing fallback."""
import json
import re
from datetime import timedelta
from ...model_adapter import MockAdapter, ModelReply
from ..domain.rules import ChangeError


def mock_change_adapter(working, intent):
    items = {i.id:i for i in working.payload.items}
    operations = []
    if intent == '第二天安排轻松一点':
        day = working.requirements.start_date + timedelta(days=1)
        targets = sorted((i for i in items.values() if i.kind=='activity' and i.start.date()==day),key=lambda i:i.start)
        if not targets:
            raise ChangeError('unsupported_change_intent')
        intent = f'替换活动 {targets[0].id}'
    match = re.fullmatch(r'(删除活动|替换活动|调整活动时间|添加活动|更换酒店|修改活动标题) ([A-Za-z0-9_-]{1,80})',intent)
    if not match:
        raise ChangeError('unsupported_change_intent')
    command,target = match.groups()
    old = items.get(target)
    if old is None or (old.kind != ('hotel' if command=='更换酒店' else 'activity')):
        raise ChangeError('unsupported_change_intent')
    if command=='删除活动':
        operations=[dict(operation='delete_item',target_item_id=target)]
    else:
        item=old.model_dump(mode='json')
        if command in ('调整活动时间','添加活动'):
            item.update(start=(old.start+timedelta(hours=3)).isoformat(),end=(old.end+timedelta(hours=3)).isoformat())
        if command=='添加活动':
            item['id']=target[:60]+'_added';item['resource_id']=target[:60]+'_mock_added'
        if command in ('替换活动','更换酒店'):
            item['resource_id']=target[:60]+'_mock_alternative'
        item['title']=('模拟替代酒店' if old.kind=='hotel' else '模拟轻松散步') if command!='调整活动时间' else old.title
        costs=[c.model_dump(mode='json') for c in working.payload.costs if c.item_id==target]
        if command!='修改活动标题':
            for c in costs:
                if command=='添加活动':c['id']=c['id'][:60]+'_added'
                c.update(item_id=item['id'],amount_cents=None,category='unknown',unknown_reason='Mock 修改后的费用尚未核验。',evidence_id=None,is_mock=True)
                c['query'].update(resource_id=item['resource_id'],start_date=item['start'][:10],end_date=item['end'][:10])
        operations=[dict(operation='add_item',item=item,costs=costs)] if command=='添加活动' else [dict(operation='replace_hotel' if old.kind=='hotel' else 'replace_activity',target_item_id=target,replacement=item,costs=costs)]
    data=dict(base_working_revision=working.revision,explanation='确定性 Mock 修改；请审阅完整差异、未知费用和风险后决定。',operations=operations)
    return MockAdapter(ModelReply(json.dumps(data,ensure_ascii=False)))
