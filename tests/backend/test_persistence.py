"""Real PostgreSQL consistency tests. Fresh, retained M0-02 database required."""
import asyncio
from contextlib import asynccontextmanager
import os
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql
import pytest
from pydantic import ValidationError

from easytravel.contracts import PlanDraftOutput
from easytravel.backend.application.plans import PlanService
from easytravel.backend.domain.errors import ResourceNotFound
from easytravel.backend.domain.models import SafeChangeOutput, WorkingDraft
from easytravel.backend.domain.rules import ChangeError
from easytravel.backend.infrastructure.database import (
    DatabaseSettings, DatabaseUnavailable, connection, load_database_settings)
from easytravel.backend.infrastructure.migrate import MIGRATIONS, MigrationError, upgrade
from easytravel.backend.infrastructure.repository import unit_of_work


def run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(asyncio.wait_for(coro, 45))


def identifier():
    return str(uuid4())


async def fingerprint():
    """Read every existing experiment table; no setup/DDL/cleanup on that database."""
    from easytravel.spike02.database import business_connection
    result = {}
    async with business_connection() as conn:
        rows = await (await conn.execute("""SELECT schemaname,tablename FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2""")).fetchall()
        for row in rows:
            query = sql.SQL("SELECT count(*) AS n, md5(COALESCE(string_agg(h, '' ORDER BY h),'')) AS hash "
                            "FROM (SELECT md5(row_to_json(t)::text) AS h FROM {}.{} t) x").format(
                                sql.Identifier(row["schemaname"]), sql.Identifier(row["tablename"]))
            result[row["schemaname"] + "." + row["tablename"]] = await (await conn.execute(query)).fetchone()
    return result


@pytest.fixture(scope="module")
def database():
    settings = load_database_settings()
    target = conninfo_to_dict(settings.url.get_secret_value())
    assert target["host"] == "127.0.0.1" and target["port"] == "55432"
    assert target["dbname"].startswith("easytravel_m002_test_")
    assert target["user"].startswith("easytravel_m002_")
    before = run(fingerprint())

    async def initialize_empty():
        async with connection(settings) as conn:
            row = await (await conn.execute("""SELECT count(*) AS n FROM pg_tables
                WHERE schemaname NOT IN ('pg_catalog','information_schema')""")).fetchone()
            assert row["n"] == 0, "Use a freshly provisioned database; do not clean existing data"
        upgrades = await asyncio.gather(upgrade(settings), upgrade(settings))
        assert sorted(upgrades, key=len) == [[], [p.name for p in sorted(MIGRATIONS.glob("*.sql"))]]
        async with connection(settings) as conn:
            tables = await (await conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname='easytravel'")).fetchall()
            assert len(tables) == 19
        print("Fresh empty database initialized with 001-006; 19 formal tables; concurrent installers serialized")

    run(initialize_empty())
    yield settings
    after = run(fingerprint())
    assert before == after, "Formal tests modified existing experiment data"
    print(f"Isolation verified: {len(before)} experiment/Checkpoint tables unchanged (counts + full-row hashes)")


@pytest.fixture
def case(database, payload, requirements):
    service = PlanService(unit_of_work(database))
    actor, trip_id = "owner_" + uuid4().hex, identifier()
    draft = run(service.create_trip(trip_id, PlanDraftOutput.model_validate(payload), requirements, actor=actor))
    return service, actor, draft


def change(draft, target="activity-0", title="新标题"):
    item = next(i for i in draft.payload.items if i.id == target)
    replacement = item.model_dump(mode="json") | {"title": title}
    return SafeChangeOutput.model_validate(dict(base_working_revision=draft.revision, explanation="更新标题",
        operations=[dict(operation="replace_hotel" if target == "hotel" else "replace_activity",
                         target_item_id=target, replacement=replacement,
                         costs=[c.model_dump(mode="json") for c in draft.payload.costs if c.item_id == target])]))


async def confirm(service, actor, draft, operation_id=None):
    preview = await service.confirmation_preview(draft.trip_id, actor=actor)
    return await service.confirm_plan(draft.trip_id, actor=actor, operation_id=operation_id or identifier(),
        expected_revision=preview["working_revision"], seen_disclosure_hash=preview["disclosure_hash"],
        explicit_confirmation=True)


def test_fresh_schema_and_repeat_upgrade_preserves_data(database, case):
    service, actor, draft = case
    async def scenario():
        result = await confirm(service, actor, draft)
        snapshot = await service.get_snapshot(result["latest_confirmed_version_id"], actor=actor)
        assert await upgrade(database) == []
        assert await upgrade(database) == []
        assert await service.get_snapshot(snapshot.snapshot_id, actor=actor) == snapshot
        assert (await service.get_working(draft.trip_id, actor=actor)).payload == draft.payload
    run(scenario())
    result = subprocess.run([sys.executable, "-m", "easytravel.backend.infrastructure.migrate"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Database migration succeeded; applied="
    print(result.stdout.strip())


def test_connection_scope_closes_after_commit_and_exception(database):
    async def scenario():
        async with connection(database) as committed, committed.transaction():
            assert (await (await committed.execute("SELECT 1 AS n")).fetchone())["n"] == 1
        assert committed.closed
        with pytest.raises(psycopg.errors.DivisionByZero):
            async with connection(database) as aborted, aborted.transaction():
                await aborted.execute("SELECT 1/0")
        assert aborted.closed
    run(scenario())


def test_schema_has_no_experiment_or_checkpoint_links(database):
    async def scenario():
        async with connection(database) as conn:
            namespaces = await (await conn.execute("""SELECT DISTINCT schemaname FROM pg_tables
                WHERE schemaname NOT IN ('pg_catalog','information_schema')""")).fetchall()
            assert namespaces == [{"schemaname": "easytravel"}]
            links = await (await conn.execute("""SELECT DISTINCT target_ns.nspname AS schema
                FROM pg_constraint c JOIN pg_class source ON source.oid=c.conrelid
                JOIN pg_namespace source_ns ON source_ns.oid=source.relnamespace
                JOIN pg_class target ON target.oid=c.confrelid
                JOIN pg_namespace target_ns ON target_ns.oid=target.relnamespace
                WHERE c.contype='f' AND source_ns.nspname='easytravel'""")).fetchall()
            assert links == [{"schema": "easytravel"}]
    run(scenario())


def test_checksum_drift_and_unknown_migration_fail(database, tmp_path):
    for source in MIGRATIONS.glob("*.sql"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    async def scenario():
        original = (MIGRATIONS / "001_initial.sql").read_text(encoding="utf-8")
        (tmp_path / "001_initial.sql").write_text(original + "\n-- drift", encoding="utf-8")
        with pytest.raises(MigrationError, match="checksum"):
            await upgrade(database, directory=tmp_path)
        unknown = tmp_path / "unknown"
        unknown.mkdir()
        (unknown / "002_unknown.sql").write_text("SELECT 1", encoding="utf-8")
        with pytest.raises(MigrationError, match="history"):
            await upgrade(database, directory=unknown)
        assert await upgrade(database) == []
    run(scenario())


def test_failed_migration_rolls_back_ddl_and_ledger(database, tmp_path):
    for source in MIGRATIONS.glob("*.sql"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    (tmp_path / "007_failure.sql").write_text(
        "CREATE TABLE easytravel.must_rollback(id integer); SELECT 1/0;", encoding="utf-8")
    async def scenario():
        with pytest.raises(psycopg.errors.DivisionByZero):
            await upgrade(database, directory=tmp_path)
        async with connection(database) as conn:
            assert (await (await conn.execute("SELECT to_regclass('easytravel.must_rollback') AS t")).fetchone())["t"] is None
            assert (await (await conn.execute("SELECT count(*) AS n FROM easytravel.schema_migrations")).fetchone())["n"] == 6
    run(scenario())


def test_working_revision_conflict_and_cas(database, case):
    service, actor, draft = case
    async def scenario():
        p1 = await service.propose_change(draft.trip_id, change(draft), actor=actor)
        p2 = await service.propose_change(draft.trip_id, change(draft, title="另一个标题"), actor=actor)
        await service.apply_working_change(p1.proposal_id, operation_id=identifier(), actor=actor)
        with pytest.raises(ChangeError, match="stale_proposal"):
            await service.apply_working_change(p2.proposal_id, operation_id=identifier(), actor=actor)
        async with unit_of_work(database)(actor) as repo:
            current = await repo.working(draft.trip_id)
            data = draft.model_dump() | {"revision": 2}
            with pytest.raises(ChangeError, match="revision_conflict"):
                await repo.advance(draft, WorkingDraft.model_validate(data))
        assert current.revision == 2
    run(scenario())


@pytest.mark.parametrize("statement", [
    "UPDATE easytravel.confirmed_snapshots SET disclosure_hash=disclosure_hash WHERE snapshot_id=%s",
    "DELETE FROM easytravel.confirmed_snapshots WHERE snapshot_id=%s",
    "UPDATE easytravel.draft_versions SET payload=payload WHERE trip_id=%s",
    "DELETE FROM easytravel.draft_versions WHERE trip_id=%s",
])
def test_immutable_snapshot_and_source_version(database, case, statement):
    service, actor, draft = case
    async def scenario():
        result = await confirm(service, actor, draft)
        key = result["latest_confirmed_version_id"] if "snapshots" in statement else draft.trip_id
        async with connection(database) as conn:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable_business_record"):
                async with conn.transaction():
                    await conn.execute(statement, (key,))
        snapshot = await service.get_snapshot(result["latest_confirmed_version_id"], actor=actor)
        assert snapshot.payload == draft.payload
    run(scenario())


def test_snapshot_remains_old_after_working_change(case):
    service, actor, draft = case
    async def scenario():
        result = await confirm(service, actor, draft)
        proposal = await service.propose_change(draft.trip_id, change(draft), actor=actor)
        await service.apply_working_change(proposal.proposal_id, operation_id=identifier(), actor=actor)
        working = await service.get_working(draft.trip_id, actor=actor)
        snapshot = await service.get_snapshot(result["latest_confirmed_version_id"], actor=actor)
        assert working.revision == 2 and snapshot.source_working_revision == 1
        assert working.payload != snapshot.payload and snapshot.payload == draft.payload
        assert working.latest_confirmed_version_id == snapshot.snapshot_id
    run(scenario())


@pytest.mark.parametrize("override,code", [
    ({"explicit_confirmation": False}, "explicit_confirmation_required"),
    ({"explicit_confirmation": 1}, "explicit_confirmation_required"),
    ({"seen_disclosure_hash": "0" * 64}, "disclosure_not_acknowledged"),
    ({"expected_revision": 2}, "stale_revision"),
])
def test_confirmation_rechecks_acknowledgement_and_revision(case, override, code):
    service, actor, draft = case
    async def scenario():
        preview = await service.confirmation_preview(draft.trip_id, actor=actor)
        args = dict(operation_id=identifier(), actor=actor, expected_revision=1,
                    seen_disclosure_hash=preview["disclosure_hash"], explicit_confirmation=True) | override
        with pytest.raises(ChangeError, match=code):
            await service.confirm_plan(draft.trip_id, **args)
        assert await service.get_working(draft.trip_id, actor=actor) == draft
    run(scenario())


def test_new_process_reads_committed_state(case):
    service, actor, draft = case
    script = '''
import asyncio, sys
from easytravel.backend.application.plans import PlanService
from easytravel.backend.infrastructure.database import load_database_settings
from easytravel.backend.infrastructure.repository import unit_of_work
async def main():
    service = PlanService(unit_of_work(load_database_settings()))
    draft = await service.get_working(sys.argv[1], actor=sys.argv[2])
    print(draft.trip_id, draft.owner_id, draft.revision)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
    runner.run(main())
'''
    result = subprocess.run([sys.executable, "-c", script, draft.trip_id, actor],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{draft.trip_id} {actor} 1"


def test_repeated_and_concurrent_operation_one_result(database, case):
    service, actor, draft = case
    async def scenario():
        proposal = await service.propose_change(draft.trip_id, change(draft), actor=actor)
        op = identifier()
        results = await asyncio.gather(*[
            service.apply_working_change(proposal.proposal_id, operation_id=op, actor=actor) for _ in range(3)])
        assert results[0] == results[1] == results[2]
        later = await service.get_working(draft.trip_id, actor=actor)
        next_proposal = await service.propose_change(draft.trip_id, change(later, title="第三版"), actor=actor)
        await service.apply_working_change(next_proposal.proposal_id, operation_id=identifier(), actor=actor)
        assert await service.apply_working_change(proposal.proposal_id, operation_id=op, actor=actor) == results[0]
        with pytest.raises(ChangeError, match="operation_id_conflict"):
            await service.apply_working_change(next_proposal.proposal_id, operation_id=op, actor=actor)
        async with connection(database) as conn:
            row = await (await conn.execute("SELECT count(*) AS n FROM easytravel.operation_receipts WHERE operation_id=%s", (op,))).fetchone()
            assert row["n"] == 1
        assert (await service.get_working(draft.trip_id, actor=actor)).revision == 3
    run(scenario())


def test_concurrent_different_operations_conflict(case):
    service, actor, draft = case
    async def scenario():
        proposals = [await service.propose_change(draft.trip_id, change(draft, title=t), actor=actor)
                     for t in ("标题甲", "标题乙")]
        results = await asyncio.gather(*[service.apply_working_change(p.proposal_id,
            operation_id=identifier(), actor=actor) for p in proposals], return_exceptions=True)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert sum(isinstance(r, ChangeError) for r in results) == 1
        assert (await service.get_working(draft.trip_id, actor=actor)).revision == 2
    run(scenario())


def test_confirmation_receipt_and_unique_snapshot(database, case):
    service, actor, draft = case
    async def scenario():
        op = identifier()
        results = await asyncio.gather(confirm(service, actor, draft, op), confirm(service, actor, draft, op))
        assert results[0] == results[1]
        second = await confirm(service, actor, draft)
        assert second["latest_confirmed_version_id"] == results[0]["latest_confirmed_version_id"]
        async with connection(database) as conn:
            row = await (await conn.execute("SELECT count(*) AS n FROM easytravel.confirmed_snapshots WHERE trip_id=%s", (draft.trip_id,))).fetchone()
            assert row["n"] == 1
    run(scenario())


def test_authorization_exact_scope_and_single_consumption(database, case):
    service, actor, draft = case
    async def scenario():
        proposal = await service.propose_change(draft.trip_id, change(draft, target="hotel"), actor=actor)
        assert proposal.level == "L3"
        with pytest.raises(ChangeError, match="specific_authorization_required"):
            await service.apply_working_change(proposal.proposal_id, operation_id=identifier(), actor=actor)
        for kwargs, code in [({"explicit_authorization": False}, "explicit_authorization_required"),
                             ({"seen_candidate_hash": "0" * 64}, "authorization_scope_mismatch"),
                             ({"item_ids": ("activity-0",)}, "authorization_scope_mismatch")]:
            args = dict(seen_candidate_hash=proposal.candidate_hash, item_ids=proposal.touched_item_ids,
                        explicit_authorization=True, actor=actor) | kwargs
            with pytest.raises(ChangeError, match=code):
                await service.authorize_change(proposal.proposal_id, **args)
        grant = await service.authorize_change(proposal.proposal_id, actor=actor,
            seen_candidate_hash=proposal.candidate_hash, item_ids=proposal.touched_item_ids, explicit_authorization=True)
        op = identifier()
        first = await service.apply_working_change(proposal.proposal_id, operation_id=op, actor=actor,
                                                   authorization_id=grant.authorization_id)
        assert await service.apply_working_change(proposal.proposal_id, operation_id=op, actor=actor,
                                                  authorization_id=grant.authorization_id) == first
        async with unit_of_work(database)(actor) as repo:
            _, consumed = await repo.authorization(grant.authorization_id)
            assert consumed == op
        async with connection(database) as conn:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="single_use"):
                async with conn.transaction():
                    await conn.execute("UPDATE easytravel.specific_authorizations SET consumed_by_operation_id=NULL WHERE authorization_id=%s", (grant.authorization_id,))
    run(scenario())


@pytest.mark.parametrize("kind", ["apply", "confirm"])
@pytest.mark.parametrize("fault_at", ["save_receipt", "commit"])
def test_database_error_rolls_back_entire_business_transaction(database, case, kind, fault_at):
    service, actor, draft = case

    class FailingRepository:
        def __init__(self, repo):
            self.repo = repo

        def __getattr__(self, name):
            return getattr(self.repo, name)

        async def save_receipt(self, *args):
            await self.repo.save_receipt(*args)
            if fault_at == "save_receipt":
                await self.repo.conn.execute("SELECT 1/0")

    @asynccontextmanager
    async def failing_uow(owner):
        async with unit_of_work(database)(owner) as repo:
            yield FailingRepository(repo)
            if fault_at == "commit":
                # Real deferred FK violation detected when transaction attempts COMMIT.
                await repo.conn.execute("SET CONSTRAINTS easytravel.working_version_fk DEFERRED")
                await repo.conn.execute("UPDATE easytravel.working_drafts SET revision=999999 WHERE trip_id=%s", (draft.trip_id,))

    async def scenario():
        op = identifier()
        failing = PlanService(failing_uow)
        if kind == "apply":
            proposal = await service.propose_change(draft.trip_id, change(draft, target="hotel"), actor=actor)
            grant = await service.authorize_change(proposal.proposal_id, actor=actor, explicit_authorization=True,
                seen_candidate_hash=proposal.candidate_hash, item_ids=proposal.touched_item_ids)
            action = failing.apply_working_change(proposal.proposal_id, operation_id=op,
                                                  actor=actor, authorization_id=grant.authorization_id)
        else:
            preview = await service.confirmation_preview(draft.trip_id, actor=actor)
            action = failing.confirm_plan(draft.trip_id, operation_id=op, actor=actor, expected_revision=1,
                seen_disclosure_hash=preview["disclosure_hash"], explicit_confirmation=True)
        with pytest.raises(psycopg.Error):
            await action
        assert await service.get_working(draft.trip_id, actor=actor) == draft
        async with unit_of_work(database)(actor) as repo:
            assert await repo.receipt(op, "0" * 64) is None
            if kind == "apply":
                assert (await repo.authorization(grant.authorization_id))[1] is None
        async with connection(database) as conn:
            row = await (await conn.execute("SELECT count(*) AS n FROM easytravel.confirmed_snapshots WHERE trip_id=%s", (draft.trip_id,))).fetchone()
            assert row["n"] == 0
            row = await (await conn.execute("SELECT count(*) AS n FROM easytravel.draft_versions WHERE trip_id=%s", (draft.trip_id,))).fetchone()
            assert row["n"] == 1
    run(scenario())


def test_owner_boundary_all_reads_and_writes(database, case):
    service, actor, draft = case
    intruder = "other_owner"
    async def scenario():
        proposal = await service.propose_change(draft.trip_id, change(draft, target="hotel"), actor=actor)
        grant = await service.authorize_change(proposal.proposal_id, actor=actor, explicit_authorization=True,
            seen_candidate_hash=proposal.candidate_hash, item_ids=proposal.touched_item_ids)
        result = await confirm(service, actor, draft)
        actions = [service.get_working(draft.trip_id, actor=intruder),
            service.get_snapshot(result["latest_confirmed_version_id"], actor=intruder),
            service.propose_change(draft.trip_id, change(draft), actor=intruder),
            service.apply_working_change(proposal.proposal_id, operation_id=identifier(), actor=intruder),
            service.authorize_change(proposal.proposal_id, actor=intruder, explicit_authorization=True,
                seen_candidate_hash=proposal.candidate_hash, item_ids=proposal.touched_item_ids),
            service.confirm_plan(draft.trip_id, actor=intruder, operation_id=identifier(), expected_revision=1,
                seen_disclosure_hash="0" * 64, explicit_confirmation=True)]
        for action in actions:
            with pytest.raises(ResourceNotFound):
                await action
        async with unit_of_work(database)(intruder) as repo:
            with pytest.raises(ResourceNotFound):
                await repo.authorization(grant.authorization_id)
        async with unit_of_work(database)(actor) as repo:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with repo.conn.transaction():
                    await repo.conn.execute("""INSERT INTO easytravel.working_drafts(trip_id,owner_id,revision)
                        VALUES (%s,%s,1)""", (identifier(), intruder))
    run(scenario())


def test_unavailable_database_and_cli_explicit_failure():
    settings = DatabaseSettings(url="postgresql://fake:private_password@127.0.0.1:1/easytravel_m002_test_unavailable")
    async def scenario():
        service = PlanService(unit_of_work(settings))
        with pytest.raises(DatabaseUnavailable) as error:
            await service.get_working("missing", actor="owner")
        assert "private_password" not in str(error.value)
        with pytest.raises(DatabaseUnavailable):
            await upgrade(settings)
    run(scenario())
    env = os.environ.copy() | {"EASYTRAVEL_DATABASE_URL": settings.url.get_secret_value()}
    result = subprocess.run([sys.executable, "-m", "easytravel.backend.infrastructure.migrate"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert "Database migration failed: DatabaseUnavailable" in result.stderr
    assert "private_password" not in result.stderr + result.stdout
    assert "succeeded" not in result.stdout


@pytest.mark.parametrize("url", ["", "postgresql://a:secret@127.0.0.1:55432/easytravel_env00_test",
    "dbname=x", "postgresql://a:secret@localhost:5432/postgres",
    "postgresql://a:secret@localhost:5432/formal?options=-csearch_path=public"])
def test_database_config_rejects_implicit_or_experiment_target(url):
    with pytest.raises(ValidationError) as error:
        DatabaseSettings(url=url)
    assert "secret" not in str(error.value)


def test_database_config_explicit_and_secret_safe():
    with pytest.raises(ValueError, match="EASYTRAVEL_DATABASE_URL"):
        load_database_settings({"EASYTRAVEL_TEST_DATABASE_URL": "ignored"})
    settings = load_database_settings({"EASYTRAVEL_DATABASE_URL": "postgresql://user:secret@localhost:5432/formal"})
    assert "secret" not in repr(settings)


def test_domain_and_application_do_not_import_drivers_or_experiments():
    script = '''
import sys
class Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'psycopg', 'fastapi', 'langgraph'} or fullname.startswith((
            'easytravel.spike', 'easytravel.m000', 'easytravel.backend.infrastructure')):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from easytravel.backend.domain import models, rules
from easytravel.backend.application.plans import PlanService
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
