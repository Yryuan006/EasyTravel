"""Read-only history; never evaluate today's budget or risk rules."""
import base64
import hmac
import json
import secrets
from datetime import datetime
from ..domain.snapshots import SnapshotPage, SnapshotSummary
from .ports import SnapshotUnitOfWork

# Local single-process deployment. Restart invalidates cursors, not saved history.
_CURSOR_KEY = secrets.token_bytes(32)


class InvalidCursor(ValueError):
    pass


class SnapshotService:
    def __init__(self, uow: SnapshotUnitOfWork, *, cursor_key=_CURSOR_KEY):
        self.uow, self.cursor_key = uow, cursor_key

    def encode(self, trip_id, actor, item):
        raw = json.dumps([1, trip_id, actor, item.confirmed_at.isoformat(), item.snapshot_id], separators=(',', ':')).encode()
        return base64.urlsafe_b64encode(raw + hmac.digest(self.cursor_key, raw, 'sha256')).decode().rstrip('=')

    def decode(self, cursor, trip_id, actor):
        try:
            if not 1 <= len(cursor) <= 500:
                raise ValueError()
            blob = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
            if base64.urlsafe_b64encode(blob).decode().rstrip('=') != cursor:
                raise ValueError()
            raw, signature = blob[:-32], blob[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.cursor_key, raw, 'sha256')):
                raise ValueError()
            version, trip, owner, at, sid = json.loads(raw)
            if (version, trip, owner) != (1, trip_id, actor):
                raise ValueError()
            return datetime.fromisoformat(at), sid
        except (ValueError, TypeError, UnicodeError):
            raise InvalidCursor() from None

    async def list(self, trip_id, *, actor, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidCursor()
        position = self.decode(cursor, trip_id, actor) if cursor is not None else None
        async with self.uow(actor) as repo:
            await repo.read_only()
            await repo.require_trip(trip_id)
            rows = await repo.snapshot_page(trip_id, limit + 1, position)
            items = [SnapshotSummary.model_validate(row) for row in rows[:limit]]
            return SnapshotPage(items=items, next_cursor=self.encode(trip_id, actor, items[-1]) if len(rows) > limit else None)

    async def get(self, trip_id, snapshot_id, *, actor):
        async with self.uow(actor) as repo:
            await repo.read_only()
            return await repo.snapshot_view(trip_id, snapshot_id)
