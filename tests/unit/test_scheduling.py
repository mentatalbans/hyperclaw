import asyncio
from datetime import datetime, timedelta, timezone
import importlib
import sqlite3

from pydantic import ValidationError
import pytest

from hyperclaw import contracts
from hyperclaw.config import ensure_root
from hyperclaw.contracts import Checkpoint, Conflict, InvalidRequest, Message, RunRequest, StorageFailure, ToolCall, canonical
from hyperclaw.store import MIGRATIONS, Store


UTC = timezone.utc
MINUTE = 60


def at(year=2026, month=1, day=1, hour=0, minute=0, second=0, microsecond=0):
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


async def schedule_request(store, *, schedule_id='daily', due=None, interval_seconds=MINUTE, **changes):
    session = changes.pop('session', None) or await store.create_session()
    values = {
        'id': schedule_id,
        'session_id': session.id,
        'generation': session.generation,
        'input': 'scheduled work',
        'next_due_at': due or at(),
        'interval_seconds': interval_seconds,
        'tools': ('workspace_read',),
    }
    values.update(changes)
    return contracts.ScheduleRequest(**values)


def scheduler(store):
    return importlib.import_module('hyperclaw.scheduling').Scheduler(store)


async def test_one_shot_creation_replay_conflict_and_listing_survive_mutable_state(tmp_path):
    store = await Store.open(tmp_path)
    try:
        session = await store.create_session()
        local_due = datetime(2026, 2, 3, 4, 5, 6, 123456,
                             tzinfo=timezone(timedelta(hours=5, minutes=30)))
        request = await schedule_request(store, schedule_id='one-shot', session=session,
                                         due=local_due, interval_seconds=None)

        created = await store.create_schedule(request)
        assert created.id == 'one-shot'
        assert created.next_due_at == datetime(2026, 2, 2, 22, 35, 6, 123456, tzinfo=UTC)
        assert created.status == 'active'
        assert created.pause_reason is None
        assert await store.create_schedule(request) == created
        assert [item.id for item in await store.schedules()] == ['one-shot']

        paused = await store.pause_schedule(created.id)
        assert paused.status == 'paused'
        assert paused.pause_reason == 'operator'
        assert (await store.create_schedule(request)).status == 'paused'
        other = await store.create_session()
        for changes in [
            {'input': 'changed'},
            {'next_due_at': created.next_due_at + timedelta(seconds=1)},
            {'interval_seconds': MINUTE},
            {'tools': ()},
            {'session_id': other.id},
        ]:
            with pytest.raises(Conflict) as caught:
                await store.create_schedule(request.model_copy(update=changes))
            assert caught.value.code == 'schedule_conflict'
    finally:
        await store.close()


async def test_due_boundary_two_ticks_enqueue_one_occurrence_run_message_and_event(tmp_path):
    store = await Store.open(tmp_path)
    try:
        due = at(microsecond=7)
        request = await schedule_request(store, schedule_id='once', due=due, interval_seconds=None)
        schedule = await store.create_schedule(request)

        assert await scheduler(store).tick(due - timedelta(microseconds=1)) == []
        ticked = await asyncio.gather(scheduler(store).tick(due), scheduler(store).tick(due))
        assert sum(map(len, ticked)) == 1
        run_id = next(ids[0] for ids in ticked if ids)

        occurrences = await store.schedule_occurrences(schedule.id)
        assert [(item.schedule_id, item.nominal_due_at, item.run_id) for item in occurrences] == [
            ('once', due, run_id),
        ]
        run = await store.get_run(run_id)
        assert run.status == 'queued'
        assert run.request.text == 'scheduled work'
        assert run.request.tools == ('workspace_read',)
        assert run.request.retry_of is None
        assert run.request.request_id.startswith('schedule:')
        assert await store._call(lambda: [tuple(row) for row in store._db.execute(
            'SELECT role,content FROM messages WHERE run_id=?', (run_id,)).fetchall()]) == [
                ('user', 'scheduled work'),
            ]
        events = await store.events(run_id)
        assert [(event.kind, event.data) for event in events] == [
            ('run.queued', {'schedule_id': 'once', 'nominal_due_at': '2026-01-01T00:00:00.000007+00:00'}),
        ]
        completed = await store.get_schedule(schedule.id)
        assert completed.status == 'completed'
        assert completed.next_due_at == due
        assert await store.create_schedule(request) == completed
        with pytest.raises(Conflict) as caught:
            await store.retarget_schedule(schedule.id, 0, 0)
        assert caught.value.code == 'schedule_completed'
    finally:
        await store.close()


async def test_long_downtime_coalesces_once_and_keeps_nominal_fixed_cadence(tmp_path):
    store = await Store.open(tmp_path)
    try:
        due = at()
        request = await schedule_request(store, schedule_id='interval', due=due)
        schedule = await store.create_schedule(request)
        late = at(2027, 1, 1, second=37)

        first_ids = await scheduler(store).tick(late)
        assert len(first_ids) == 1
        assert (await store.get_schedule(schedule.id)).next_due_at == at(2027, 1, 1, minute=1)
        assert await scheduler(store).tick(late) == []

        await store.finish(first_ids[0], 'cancelled')
        second_ids = await scheduler(store).tick(at(2027, 1, 1, minute=1))
        assert len(second_ids) == 1
        assert second_ids != first_ids
        assert (await store.get_run(second_ids[0])).request.retry_of is None
        assert [item.nominal_due_at for item in await store.schedule_occurrences(schedule.id)] == [
            due,
            at(2027, 1, 1, minute=1),
        ]
        assert (await store.get_schedule(schedule.id)).next_due_at == at(2027, 1, 1, minute=2)
    finally:
        await store.close()


@pytest.mark.parametrize('occupied_status', ['queued', 'running', 'waiting_approval'])
async def test_busy_session_leaves_due_schedule_and_reservation_untouched(tmp_path, occupied_status):
    store = await Store.open(tmp_path)
    try:
        due = at()
        session = await store.create_session()
        request = await schedule_request(store, schedule_id='blocked', session=session, due=due)
        await store.create_schedule(request)
        manual = await store.submit(RunRequest(session_id=session.id, generation=0,
            request_id='manual', text='occupy the session'))
        if occupied_status != 'queued':
            await store.next_run()
        if occupied_status == 'waiting_approval':
            call = ToolCall(id='write', name='workspace_write', arguments={'path': 'x', 'content': 'y'})
            await store.save_checkpoint(manual.id, Checkpoint(messages=[Message(role='user', content='manual')],
                                                               pending_calls=[call]))
            invocation = await store.prepare_invocation(manual.id, call, 'policy', 'workspace', 'write')
            await store.require_approval(invocation.id)
        assert (await store.get_run(manual.id)).status == occupied_status

        assert await scheduler(store).tick(due) == []
        blocked = await store.get_schedule('blocked')
        assert blocked.status == 'active'
        assert blocked.next_due_at == due
        assert await store.schedule_occurrences('blocked') == []

        await store.finish(manual.id, 'cancelled')
        assert len(await scheduler(store).tick(due)) == 1
    finally:
        await store.close()


async def test_enqueue_failure_rolls_back_reservation_run_message_event_and_schedule(tmp_path):
    store = await Store.open(tmp_path)
    try:
        due = at()
        await store.create_schedule(await schedule_request(store, schedule_id='atomic', due=due))
        await store._call(lambda: store._db.execute(
            "CREATE TRIGGER fail_scheduled_enqueue BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        ))

        with pytest.raises(StorageFailure):
            await scheduler(store).tick(due)

        schedule = await store.get_schedule('atomic')
        assert schedule.status == 'active'
        assert schedule.next_due_at == due
        assert await store.schedule_occurrences('atomic') == []
        counts = await store._call(lambda: tuple(store._db.execute(
            f'SELECT count(*) FROM {table}').fetchone()[0]
            for table in ('runs', 'messages', 'events')))
        assert counts == (0, 0, 0)
    finally:
        await store.close()


async def test_reset_pauses_immediately_and_explicit_retarget_retains_pending_due(tmp_path):
    store = await Store.open(tmp_path)
    try:
        due = at()
        session = await store.create_session()
        request = await schedule_request(store, schedule_id='generation', session=session, due=due)
        await store.create_schedule(request)

        assert (await store.reset_session(session.id, 0)).generation == 1
        paused = await store.get_schedule('generation')
        assert paused.status == 'paused'
        assert paused.pause_reason == 'stale_generation'
        assert paused.generation == 0
        assert paused.next_due_at == due
        assert await scheduler(store).tick(due) == []

        with pytest.raises(Conflict) as caught:
            await store.retarget_schedule('generation', 1, 1)
        assert caught.value.code == 'stale_generation'
        with pytest.raises(Conflict) as caught:
            await store.retarget_schedule('generation', 0, 0)
        assert caught.value.code == 'stale_generation'

        active = await store.retarget_schedule('generation', 0, 1)
        assert active.status == 'active'
        assert active.pause_reason is None
        assert active.generation == 1
        assert active.next_due_at == due
        assert (await store.create_schedule(request)).generation == 1
        run_id = (await scheduler(store).tick(due))[0]

        paused = await store.pause_schedule('generation')
        assert paused.status == 'paused'
        assert paused.pause_reason == 'operator'
        assert (await store.get_run(run_id)).status == 'queued'
        resumed = await store.retarget_schedule('generation', 1, 1)
        assert resumed.status == 'active'
        assert resumed.next_due_at == due + timedelta(minutes=1)
    finally:
        await store.close()


async def test_uncertain_occurrence_pauses_future_work_and_cannot_be_retargeted(tmp_path):
    store = await Store.open(tmp_path)
    try:
        due = at()
        request = await schedule_request(store, schedule_id='uncertain', due=due)
        await store.create_schedule(request)
        run_id = (await scheduler(store).tick(due))[0]
        await store.next_run()
        await store.finish(run_id, 'uncertain')

        immediately_paused = await store.get_schedule('uncertain')
        assert immediately_paused.status == 'paused'
        assert immediately_paused.pause_reason == 'uncertain_effect'
        assert await scheduler(store).tick(due + timedelta(minutes=1)) == []
        paused = await store.get_schedule('uncertain')
        assert paused.status == 'paused'
        assert paused.pause_reason == 'uncertain_effect'
        assert paused.next_due_at == due + timedelta(minutes=1)
        assert len(await store.schedule_occurrences('uncertain')) == 1
        with pytest.raises(Conflict) as caught:
            await store.retarget_schedule('uncertain', 0, 0)
        assert caught.value.code == 'uncertain_effect'
        assert (await store.get_schedule('uncertain')).pause_reason == 'uncertain_effect'
    finally:
        await store.close()


async def test_manual_submission_cannot_enter_reserved_schedule_namespace(tmp_path):
    store = await Store.open(tmp_path)
    try:
        session = await store.create_session()
        with pytest.raises(InvalidRequest):
            await store.submit(RunRequest(session_id=session.id, generation=0,
                                          request_id='schedule:operator', text='collision'))

        longest_id = 's' * 256
        await store.create_schedule(await schedule_request(store, schedule_id=longest_id,
                                                            session=session, due=at(), interval_seconds=None))
        run_id = (await scheduler(store).tick(at()))[0]
        internal_id = (await store.get_run(run_id)).request.request_id
        assert internal_id.startswith('schedule:')
        assert len(internal_id) <= 256
    finally:
        await store.close()


def test_schedule_contracts_reject_invalid_input_tools_intervals_and_times():
    valid = {
        'id': 'schedule',
        'session_id': 'session',
        'generation': 0,
        'input': 'work',
        'next_due_at': at(),
        'interval_seconds': MINUTE,
        'tools': ('workspace_read',),
    }
    invalid = [
        {'input': '   '},
        {'tools': ('unknown',)},
        {'tools': ('workspace_read', 'workspace_read')},
        {'interval_seconds': 0},
        {'interval_seconds': 31_536_001},
        {'interval_seconds': True},
        {'next_due_at': datetime(2026, 1, 1)},
        {'next_due_at': at(1969, 12, 31, hour=23, minute=59, second=59)},
        {'next_due_at': at(9999, 1, 1)},
    ]
    for changes in invalid:
        with pytest.raises(ValidationError):
            contracts.ScheduleRequest(**dict(valid, **changes))

    for value in [
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14))),
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone(-timedelta(hours=12))),
    ]:
        with pytest.raises(ValidationError):
            contracts.ScheduleRequest(**dict(valid, next_due_at=value))

    assert contracts.ScheduleRetarget(expected_generation=0, generation=1).generation == 1
    with pytest.raises(ValidationError):
        contracts.ScheduleRetarget(expected_generation=True, generation=1)


async def test_store_revalidates_constructed_schedule_and_scheduler_tick_time(tmp_path):
    store = await Store.open(tmp_path)
    try:
        session = await store.create_session()
        malformed = contracts.ScheduleRequest.model_construct(
            id='bad', session_id=session.id, generation=0, input='', next_due_at=at(),
            interval_seconds=0, tools=('unknown',))
        with pytest.raises(InvalidRequest):
            await store.create_schedule(malformed)
        with pytest.raises(InvalidRequest):
            await scheduler(store).tick(datetime(2026, 1, 1))
    finally:
        await store.close()


async def test_timestamps_use_fixed_microseconds_and_future_overflow_completes(tmp_path):
    store = await Store.open(tmp_path)
    try:
        due = at(9998, 12, 31, hour=23, minute=59, second=59)
        await store.create_schedule(await schedule_request(store, schedule_id='last', due=due,
                                                            interval_seconds=31_536_000))
        run_ids = await scheduler(store).tick(due)
        assert len(run_ids) == 1
        assert (await store.get_schedule('last')).status == 'completed'
        stored = await store._call(lambda: (
            store._db.execute('SELECT next_due_at FROM schedules WHERE id=?', ('last',)).fetchone()[0],
            store._db.execute('SELECT nominal_due_at FROM schedule_occurrences WHERE schedule_id=?',
                              ('last',)).fetchone()[0],
        ))
        assert stored == (
            '9998-12-31T23:59:59.000000+00:00',
            '9998-12-31T23:59:59.000000+00:00',
        )
    finally:
        await store.close()


async def test_v2_additive_migration_and_reopen_preserve_schedule_and_occurrence(tmp_path):
    ensure_root(tmp_path)
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        for migration_number, (_, statements) in enumerate(MIGRATIONS[:2], start=1):
            for statement in statements:
                db.execute(statement)
            db.execute('UPDATE schema_version SET version=?', (migration_number,))
        db.execute("INSERT INTO sessions VALUES ('existing',0)")

    store = await Store.open(tmp_path)
    request = contracts.ScheduleRequest(id='migrated', session_id='existing', generation=0,
        input='after migration', next_due_at=at(), interval_seconds=None, tools=())
    try:
        assert await store._call(lambda: store._db.execute(
            'SELECT version FROM schema_version').fetchone()[0]) == 5
        assert await store._call(lambda: store._db.execute('PRAGMA foreign_key_check').fetchall()) == []
        assert list(tmp_path.glob('backup-v2-*.sqlite3')) == []
        await store.create_schedule(request)
        run_id = (await scheduler(store).tick(at()))[0]
    finally:
        await store.close()

    reopened = await Store.open(tmp_path)
    try:
        assert (await reopened.get_schedule('migrated')).status == 'completed'
        assert [item.run_id for item in await reopened.schedule_occurrences('migrated')] == [run_id]
        assert (await reopened.get_run(run_id)).request.text == 'after migration'
    finally:
        await reopened.close()


async def test_v2_reserved_request_id_collision_does_not_become_schedule_provenance(tmp_path):
    ensure_root(tmp_path)
    legacy_request = RunRequest(session_id='existing', generation=0,
        request_id='schedule:6bcb8be5f9958ca020a8112e99da41cdf7ae66744ddb8f14089d38e14a4ba873',
        text='after migration', tools=())
    with sqlite3.connect(tmp_path / 'runtime.sqlite3') as db:
        for migration_number, (_, statements) in enumerate(MIGRATIONS[:2], start=1):
            for statement in statements:
                db.execute(statement)
            db.execute('UPDATE schema_version SET version=?', (migration_number,))
        db.execute("INSERT INTO sessions VALUES ('existing',0)")
        db.execute("""INSERT INTO runs(
            id,session_id,generation,request_id,payload_json,status,created_at
            ) VALUES ('legacy-run','existing',0,?,?,'cancelled','2026-01-01T00:00:00+00:00')""",
            (legacy_request.request_id, canonical(legacy_request.model_dump())))
        db.execute("INSERT INTO messages(run_id,role,content) VALUES ('legacy-run','user','after migration')")
        db.execute("INSERT INTO events VALUES ('legacy-run',1,'run.queued','2026-01-01T00:00:00+00:00','{}')")

    store = await Store.open(tmp_path)
    try:
        request = contracts.ScheduleRequest(id='migrated', session_id='existing', generation=0,
            input='after migration', next_due_at=at(), interval_seconds=None, tools=())
        await store.create_schedule(request)

        run_id = (await scheduler(store).tick(at()))[0]

        assert run_id != 'legacy-run'
        assert [item.run_id for item in await store.schedule_occurrences('migrated')] == [run_id]
        assert (await store.get_run('legacy-run')).status == 'cancelled'
        assert (await store.events('legacy-run'))[0].data == {}
        assert (await store.events(run_id))[0].data == {
            'schedule_id': 'migrated',
            'nominal_due_at': '2026-01-01T00:00:00.000000+00:00',
        }
    finally:
        await store.close()
