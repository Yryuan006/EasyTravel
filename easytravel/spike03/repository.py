"""Parameterised SQL access; transaction boundaries belong to the service."""
from psycopg.types.json import Jsonb

from .models import ChangeProposal, ConfirmedSnapshot, SpecificAuthorization, WorkingDraft
from .rules import ChangeError, validate_confirmations


class Repository:
    def __init__(self, connection):
        self.conn = connection

    async def working(self, trip_id):
        row = await (await self.conn.execute(
            "SELECT * FROM working_drafts WHERE trip_id=%s", (trip_id,))).fetchone()
        if row is None:
            raise ChangeError("trip_not_found")
        draft = WorkingDraft.model_validate({k: row[k] for k in (
            "trip_id", "owner_id", "revision", "requirements", "payload",
            "item_confirmations", "latest_confirmed_version_id")})
        if row["requirements_revision"] != draft.requirements.requirements_revision:
            raise ChangeError("requirements_revision_mismatch")
        validate_confirmations(draft)
        return draft

    async def snapshot(self, snapshot_id):
        row = await (await self.conn.execute(
            "SELECT data FROM confirmed_snapshots WHERE snapshot_id=%s", (snapshot_id,))).fetchone()
        if row is None:
            raise ChangeError("snapshot_not_found")
        return ConfirmedSnapshot.model_validate(row["data"])

    async def proposal(self, proposal_id):
        row = await (await self.conn.execute(
            "SELECT data FROM change_proposals WHERE proposal_id=%s", (proposal_id,))).fetchone()
        if row is None:
            raise ChangeError("proposal_not_found")
        return ChangeProposal.model_validate(row["data"])

    async def authorization(self, authorization_id):
        row = await (await self.conn.execute(
            "SELECT * FROM specific_authorizations WHERE authorization_id=%s", (authorization_id,))).fetchone()
        if row is None:
            raise ChangeError("authorization_not_found")
        return SpecificAuthorization.model_validate(row["data"]), row["consumed_by_operation_id"]

    async def receipt(self, operation_id, request_hash):
        row = await (await self.conn.execute(
            "SELECT * FROM operation_receipts WHERE operation_id=%s", (operation_id,))).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ChangeError("operation_id_conflict")
        return row["result"]

    async def save_receipt(self, operation_id, trip_id, kind, request_hash, result):
        await self.conn.execute(
            "INSERT INTO operation_receipts(operation_id,trip_id,operation_type,request_hash,result) VALUES (%s,%s,%s,%s,%s)",
            (operation_id, trip_id, kind, request_hash, Jsonb(result)))
