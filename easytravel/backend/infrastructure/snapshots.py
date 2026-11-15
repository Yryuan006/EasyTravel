"""Minimal owner/parent-scoped history SQL on the existing confirmation UoW."""
from contextlib import asynccontextmanager
from .confirmations import PostgresConfirmationRepository
from .database import connection, DatabaseUnavailable
from ..domain.models import ConfirmedSnapshot
from ..domain.intake import DraftContext
from ..domain.confirmation import SnapshotView, RiskDisclosure
import psycopg


class PostgresSnapshotRepository(PostgresConfirmationRepository):
    async def require_trip(self, trip_id):
        await self.one('SELECT trip_id FROM easytravel.trips WHERE trip_id=%s AND owner_id=%s', (trip_id, self.actor))

    async def snapshot_page(self, trip_id, limit, position):
        params = [trip_id, self.actor]
        clause = ''
        if position is not None:
            clause = ' AND (confirmed_at,snapshot_id)<(%s,%s)'
            params.extend(position)
        params.append(limit)
        return await (await self.conn.execute('''SELECT snapshot_id,source_working_revision,confirmed_at,budget
            FROM easytravel.confirmed_snapshots WHERE trip_id=%s AND confirmed_by=%s''' + clause +
            ' ORDER BY confirmed_at DESC,snapshot_id DESC LIMIT %s', params)).fetchall()

    async def snapshot_view(self, trip_id, snapshot_id):
        row = await self.one('''SELECT s.*,v.requirements,v.payload,v.item_confirmations,
            d.context AS saved_context,d.disclosure AS saved_disclosure,c.context AS source_context
            FROM easytravel.confirmed_snapshots s JOIN easytravel.draft_versions v
            ON (s.trip_id,s.confirmed_by,s.source_working_revision)=(v.trip_id,v.owner_id,v.revision)
            JOIN easytravel.trips t ON (s.trip_id,s.confirmed_by)=(t.trip_id,t.owner_id)
            LEFT JOIN easytravel.confirmation_disclosures d
            ON (s.snapshot_id,s.trip_id,s.confirmed_by)=(d.snapshot_id,d.trip_id,d.owner_id)
            LEFT JOIN easytravel.draft_contexts c
            ON (s.trip_id,s.confirmed_by,s.source_working_revision)=(c.trip_id,c.owner_id,c.working_revision)
            WHERE s.snapshot_id=%s AND s.trip_id=%s AND s.confirmed_by=%s''', (snapshot_id,trip_id,self.actor))
        saved_context, source_context = row.pop('saved_context'), row.pop('source_context')
        context = saved_context if saved_context is not None else source_context
        disclosure = row.pop('saved_disclosure')
        return SnapshotView(snapshot=ConfirmedSnapshot.model_validate(row),
            context=DraftContext.model_validate(context) if context is not None else None,
            risk_disclosure=RiskDisclosure.model_validate(disclosure) if disclosure is not None else None,
            readiness='NOT_ASSESSED', booked=False)


def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        try:
            async with connection(settings) as conn, conn.transaction():
                yield PostgresSnapshotRepository(conn, actor)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.InvalidSchemaName):
            raise DatabaseUnavailable('History migration is required') from None
    return scope
