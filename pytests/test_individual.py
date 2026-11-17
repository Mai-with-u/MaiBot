"""Individual 建档、数据库升级和原子合并行为。"""

from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

import json
import pytest

from src.common.data_models.person_info_data_model import MaiPersonInfo
from src.common.database.database_model import Individual, IndividualMergeEvent, PersonInfo
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v52_to_v53 import migrate_v52_to_v53
from src.person_info.individual import (
    IndividualProfile,
    get_individual_members,
    merge_individuals as commit_merge,
    preview_individual_merge,
    profile_of,
)
from src.common.database.migrations.v53_to_v54 import migrate_v53_to_v54


def merge_individuals(session, *, target_individual_id, expected_members):
    try:
        preview = preview_individual_merge(
            session, target_individual_id=target_individual_id, individual_ids=list(set(expected_members.values()))
        )
        revision, profile, conflicts = preview.revision, preview.profile, preview.conflicts
    except LookupError:
        revision, profile, conflicts = "stale", profile_of(session.get(Individual, target_individual_id)), []
    return commit_merge(
        session,
        target_individual_id=target_individual_id,
        expected_members=expected_members,
        expected_revision=revision,
        profile=profile,
        resolved_conflicts=conflicts,
    )


@pytest.fixture
def engine():
    database = create_engine("sqlite://")

    @event.listens_for(database, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    for model in (Individual, IndividualMergeEvent, PersonInfo):
        model.__table__.create(database)
    yield database
    database.dispose()


def add_people(session):
    people = [
        PersonInfo(
            person_id=f"p{i}",
            platform=platform,
            user_id=str(i),
            user_nickname=f"用户{i}",
        )
        for i, platform in enumerate(("qq", "telegram", "qq"))
    ]
    session.add_all(people)
    session.commit()
    return people


def test_default_individual_and_model_roundtrip(engine):
    with Session(engine) as session:
        people = add_people(session)
        assert len({person.individual_id for person in people}) == 3
        for person in people:
            assert session.get(Individual, person.individual_id).display_name == person.user_nickname
            assert MaiPersonInfo.from_db_instance(person).to_db_instance().individual_id == person.individual_id
        people[0].user_nickname = "新昵称"
        original = people[0].individual_id
        session.commit()
        assert people[0].individual_id == original


def test_merge_keeps_accounts_facts_and_includes_existing_members(engine):
    with Session(engine) as session:
        people = add_people(session)
        ids = [person.individual_id for person in people]
        snapshot = {person.person_id: person.individual_id for person in people[:2]}
        merge_individuals(session, target_individual_id=ids[0], expected_members=snapshot)
        session.commit()
        assert session.get(Individual, ids[1]) is None
        assert len(get_individual_members(session, ids[0])) == 2
        # 再合并一个账号时，不允许漏掉已经关联的成员。
        with pytest.raises(ValueError, match="成员已变化"):
            merge_individuals(session, target_individual_id=ids[0], expected_members={"p0": ids[0], "p2": ids[2]})
        snapshot = {person.person_id: person.individual_id for person in people}
        merge_individuals(session, target_individual_id=ids[0], expected_members=snapshot)
        session.commit()
        assert len(get_individual_members(session, ids[0])) == 3
        assert [(person.platform, person.user_id) for person in people] == [
            ("qq", "0"),
            ("telegram", "1"),
            ("qq", "2"),
        ]
        events = session.exec(select(IndividualMergeEvent).order_by(IndividualMergeEvent.id)).all()
        assert len(events) == 2
        assert json.loads(events[0].members_json) == {"p0": ids[0], "p1": ids[1]}


def test_stale_preview_and_transaction_rollback(engine):
    with Session(engine) as session:
        people = add_people(session)
        original = {person.person_id: person.individual_id for person in people}
        target = people[0].individual_id
        merge_individuals(session, target_individual_id=target, expected_members=original)
        session.rollback()
        assert {person.person_id: person.individual_id for person in people} == original
        assert session.exec(select(IndividualMergeEvent)).all() == []
        merge_individuals(session, target_individual_id=target, expected_members=original)
        session.commit()
        with pytest.raises(ValueError, match="成员已变化"):
            merge_individuals(session, target_individual_id=target, expected_members=original)


def test_v52_backfill_preserves_rows_and_rejects_missing_ownership():
    database = create_engine("sqlite://")
    with database.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql(
            "CREATE TABLE person_info (id INTEGER PRIMARY KEY, person_id TEXT UNIQUE, "
            "person_name TEXT, user_nickname TEXT, user_id TEXT, memory_points TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO person_info VALUES (1,'p1','人物','昵称','100','事实'), (2,'p2',NULL,'昵称2','200','事实2')"
        )
        context = MigrationExecutionContext(
            connection=connection,
            current_version=52,
            target_version=53,
            step_index=1,
            step_name="v52_to_v53",
            total_steps=1,
        )
        migrate_v52_to_v53(context)
        first = connection.exec_driver_sql("SELECT * FROM person_info ORDER BY id").all()
        migrate_v52_to_v53(context)
        assert connection.exec_driver_sql("SELECT * FROM person_info ORDER BY id").all() == first
        assert first[0].individual_id != first[1].individual_id
        assert first[0].memory_points == "事实"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE person_info SET individual_id=NULL WHERE id=1"))
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE person_info SET individual_id='missing' WHERE id=1"))
    database.dispose()


def test_webui_merge_commits_and_rejects_stale_request(engine, monkeypatch):
    from src.webui.routers import person as router

    @contextmanager
    def isolated_session():
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(router, "get_db_session", isolated_session)
    with Session(engine) as session:
        people = add_people(session)
        target = people[0].individual_id
        snapshot = {person.person_id: person.individual_id for person in people}
    before = router.get_individual(target)
    assert len(before.members) == 1
    with Session(engine) as session:
        preview = preview_individual_merge(
            session, target_individual_id=target, individual_ids=list(set(snapshot.values()))
        )
    request = router.IndividualMergeRequest(
        target_individual_id=target,
        expected_members=snapshot,
        expected_revision=preview.revision,
        profile=preview.profile,
        resolved_conflicts=preview.conflicts,
    )
    result = router.merge_individual(request)
    assert len(result.members) == 3
    assert {person.individual_id for person in result.members} == {target}
    assert len(router.get_individual(target).members) == 3
    with pytest.raises(HTTPException) as error:
        router.merge_individual(request)
    assert error.value.status_code == 409


def test_person_creation_rollback_leaves_no_individual(engine):
    with Session(engine) as session:
        session.add(PersonInfo(person_id="p", platform="qq", user_id="1", user_nickname="昵称"))
        session.flush()
        assert len(session.exec(select(Individual)).all()) == 1
        session.rollback()
        assert session.exec(select(Individual)).all() == []


def test_directory_pages_complete_groups_and_can_list_all_accounts(engine, monkeypatch):
    from src.webui.routers import person as router

    @contextmanager
    def isolated_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(router, "get_db_session", isolated_session)
    with Session(engine) as session:
        people = add_people(session)
        target = people[0].individual_id
        merge_individuals(
            session,
            target_individual_id=target,
            expected_members={person.person_id: person.individual_id for person in people[:2]},
        )
        session.commit()

    def directory(**overrides):
        params = dict(
            page=1, page_size=1, view="individuals", search="", platform=None, is_known=None, linked_only=False
        )
        return router.get_individual_directory(**(params | overrides))

    # 按账号搜索并筛选平台后，仍返回另一平台的关联账号，而不是截断成员关系。
    filtered = directory(search="用户1", platform="telegram")
    assert filtered.total == 1
    assert filtered.data[0].individual_id == target
    assert filtered.data[0].member_count == 2
    assert {person.person_id for person in filtered.data[0].members} == {"p0", "p1"}
    assert directory(linked_only=True).total == 1
    assert directory(search=target).total == 1
    assert directory(search="不存在").data == []
    first, second = directory(), directory(page=2)
    assert first.total == second.total == 2
    assert first.data[0].individual_id != second.data[0].individual_id
    accounts = directory(view="persons", page_size=10)
    assert accounts.total == 3
    assert all(len(entry.members) == 1 for entry in accounts.data)
    assert [entry.member_count for entry in accounts.data] == [2, 2, 1]
    assert directory(view="persons", platform="telegram").total == 1
    stats = router.get_person_stats()["data"]
    assert (stats["individuals"], stats["linked_individuals"], stats["total"]) == (2, 1, 3)


def test_profile_conflicts_require_resolution_and_keep_all_sources(engine):
    with Session(engine) as session:
        people = add_people(session)
        first, second = people[:2]
        second.individual.is_known = True
        session.commit()
        ids = [first.individual_id, second.individual_id]
        preview = preview_individual_merge(session, target_individual_id=ids[0], individual_ids=ids)
        assert set(preview.conflicts) == {"display_name", "is_known"}
        args = dict(
            target_individual_id=ids[0],
            expected_members=preview.members,
            expected_revision=preview.revision,
            profile=IndividualProfile(display_name="统一称呼", is_known=True),
        )
        with pytest.raises(ValueError, match="全部资料冲突"):
            commit_merge(session, **args, resolved_conflicts=["display_name"])
        assert session.exec(select(IndividualMergeEvent)).all() == []
        target = commit_merge(session, **args, resolved_conflicts=preview.conflicts)
        session.commit()
        session.expire_all()
        members = get_individual_members(session, target.individual_id)
        assert {person.individual.display_name for person in members} == {"统一称呼"}
        assert all(person.individual.is_known for person in members)
        assert [person.user_nickname for person in members] == ["用户0", "用户1"]
        assert {origin["person_id"] for origin in json.loads(target.profile_sources_json)} == {"p0", "p1"}
        event = session.exec(select(IndividualMergeEvent)).one()
        assert json.loads(event.profiles_json)["after"] == args["profile"].model_dump()
        assert len(json.loads(event.profiles_json)["before"]) == 2


def test_profile_edit_after_preview_rejects_merge(engine):
    with Session(engine) as session:
        people = add_people(session)
        ids = [person.individual_id for person in people]
        preview = preview_individual_merge(session, target_individual_id=ids[0], individual_ids=ids)
        people[1].individual.display_name = "预览后的新修改"
        session.commit()
        with pytest.raises(ValueError, match="资料或成员已变化"):
            commit_merge(
                session,
                target_individual_id=ids[0],
                expected_members=preview.members,
                expected_revision=preview.revision,
                profile=preview.profile,
                resolved_conflicts=preview.conflicts,
            )
        assert len(session.exec(select(Individual)).all()) == 3


def test_edit_via_account_changes_shared_profile_and_keeps_account_origin(engine, monkeypatch):
    from src.webui.routers import person as router

    @contextmanager
    def isolated_session():
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(router, "get_db_session", isolated_session)
    with Session(engine) as session:
        people = add_people(session)
        target = people[0].individual_id
        merge_individuals(
            session,
            target_individual_id=target,
            expected_members={person.person_id: person.individual_id for person in people},
        )
        session.commit()
        old = profile_of(session.get(Individual, target))
    request = router.PersonUpdateRequest(
        expected_individual_id=target,
        expected_profile=old,
        person_name="新人物名",
        is_known=True,
        nickname="账号昵称",
    )
    response = router.update_person("p1", request)
    assert response.data.person_name == "新人物名"
    assert response.data.nickname == "账号昵称"
    assert router.get_person_detail("p0").data.person_name == "新人物名"
    assert router.get_person_detail("p0").data.nickname == "用户0"
    with Session(engine) as session:
        original = session.exec(select(PersonInfo).where(PersonInfo.person_id == "p1")).one()
        assert original.is_known is False
        assert original.last_known_time is None
    with pytest.raises(HTTPException) as error:
        router.update_person("p1", request)
    assert error.value.status_code == 409


def test_v54_preserves_account_sources_and_existing_individual_name():
    database = create_engine("sqlite://")
    with database.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE individuals (individual_id TEXT PRIMARY KEY, display_name TEXT, created_at DATETIME)"
        )
        connection.exec_driver_sql("CREATE TABLE individual_merge_events (id INTEGER PRIMARY KEY, members_json TEXT)")
        connection.exec_driver_sql(
            "CREATE TABLE person_info (id INTEGER PRIMARY KEY, individual_id TEXT, person_id TEXT, "
            "platform TEXT, user_id TEXT, person_name TEXT, user_nickname TEXT, name_reason TEXT, is_known BOOLEAN)"
        )
        connection.exec_driver_sql("INSERT INTO individuals VALUES ('i','保留名称','2026-01-01')")
        connection.exec_driver_sql(
            "INSERT INTO person_info VALUES (1,'i','a','qq','1','保留名称','昵称A','称呼原因',1), "
            "(2,'i','b','telegram','2','其他名称','昵称B','其他原因',0)"
        )
        before = connection.exec_driver_sql("SELECT * FROM person_info ORDER BY id").all()
        context = MigrationExecutionContext(
            connection=connection,
            current_version=53,
            target_version=54,
            step_index=1,
            step_name="v53_to_v54",
            total_steps=1,
        )
        migrate_v53_to_v54(context)
        row = connection.exec_driver_sql("SELECT * FROM individuals").mappings().one()
        assert row["display_name"] == "保留名称"
        assert row["name_reason"] == "称呼原因"
        assert row["is_known"] == 1
        assert {source["person_id"] for source in json.loads(row["profile_sources_json"])} == {"a", "b"}
        migrate_v53_to_v54(context)
        assert connection.exec_driver_sql("SELECT * FROM person_info ORDER BY id").all() == before
        assert connection.exec_driver_sql("SELECT * FROM individuals").mappings().one() == row
    database.dispose()


def test_runtime_reads_shared_profile_without_overwriting_it_on_account_sync(engine, monkeypatch):
    from src.person_info import person_info as runtime

    @contextmanager
    def isolated_session(auto_commit=True):
        with Session(engine) as session:
            yield session
            if auto_commit:
                session.commit()

    monkeypatch.setattr(runtime, "get_db_session", isolated_session)
    with Session(engine) as session:
        people = add_people(session)
        target = people[0].individual_id
        merge_individuals(
            session,
            target_individual_id=target,
            expected_members={person.person_id: person.individual_id for person in people},
        )
        session.commit()
    person = runtime.Person.__new__(runtime.Person)
    person.person_id = "p1"
    person.load_from_database()
    assert person.person_name == "用户0"
    with Session(engine) as session:
        individual = session.get(Individual, target)
        individual.display_name = "管理员刚修改的名称"
        session.add(individual)
        session.commit()
    person.add_group_nick_name("group1", "账号群名片")
    with Session(engine) as session:
        assert session.get(Individual, target).display_name == "管理员刚修改的名称"
        account = session.exec(select(PersonInfo).where(PersonInfo.person_id == "p1")).one()
        assert json.loads(account.group_cardname)[0]["group_cardname"] == "账号群名片"
        assert account.person_name is None
    person.load_from_database()
    assert person.person_name == "管理员刚修改的名称"


def test_person_list_filters_numeric_ids_before_pagination(engine, monkeypatch):
    from src.webui.routers import person as routes

    with Session(engine) as session:
        for user_id in ("12345", "00123", "ABCD1234", "1234ABCD"):
            session.add(PersonInfo(person_id=user_id, platform="qq", user_id=user_id, user_nickname="匹配用户"))
        session.commit()

    @contextmanager
    def database_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(routes, "get_db_session", database_session)
    params = dict(page=1, page_size=1, search="匹配", is_known=None, platform="qq", user_id=None)
    filtered = routes.get_person_list(**params, exclude_numeric_user_ids=True)
    assert filtered.total == 2
    assert len(filtered.data) == 1
    assert filtered.data[0].user_id in {"ABCD1234", "1234ABCD"}
    unfiltered = routes.get_person_list(**params, exclude_numeric_user_ids=False)
    assert unfiltered.total == 4
    params["user_id"] = "12345"
    assert routes.get_person_list(**params, exclude_numeric_user_ids=True).total == 0


def test_v55_drops_retired_columns_and_preserves_identity(engine):
    from sqlalchemy import inspect
    from src.common.database.migrations.v54_to_v55 import migrate_v54_to_v55

    with Session(engine) as session:
        people = add_people(session)
        owner = people[0].individual_id
        session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE person_info ADD COLUMN memory_points TEXT")
        connection.exec_driver_sql("ALTER TABLE person_info ADD COLUMN name_reason TEXT")
        connection.exec_driver_sql("ALTER TABLE individuals ADD COLUMN name_reason TEXT")
        connection.exec_driver_sql("UPDATE person_info SET memory_points='[\"old fact\"]', name_reason='old reason'")
        connection.exec_driver_sql("UPDATE individuals SET name_reason='old reason'")
        row = connection.exec_driver_sql("SELECT profile_sources_json FROM individuals WHERE individual_id=?", (owner,)).scalar_one()
        sources = json.loads(row)
        sources[0]["name_reason"] = "old reason"
        connection.exec_driver_sql("UPDATE individuals SET profile_sources_json=? WHERE individual_id=?", (json.dumps(sources), owner))
        people_before = connection.exec_driver_sql("SELECT person_id, individual_id, user_nickname FROM person_info ORDER BY id").all()
        indexes_before = inspect(connection).get_indexes("person_info")
        context = MigrationExecutionContext(connection=connection, current_version=54, target_version=55,
            step_index=1, step_name="v54_to_v55", total_steps=1)
        migrate_v54_to_v55(context)
        migrate_v54_to_v55(context)
        assert not {"name_reason", "memory_points"} & {column["name"] for column in inspect(connection).get_columns("person_info")}
        assert "name_reason" not in {column["name"] for column in inspect(connection).get_columns("individuals")}
        assert connection.exec_driver_sql("SELECT person_id, individual_id, user_nickname FROM person_info ORDER BY id").all() == people_before
        assert inspect(connection).get_indexes("person_info") == indexes_before
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        sources = json.loads(connection.exec_driver_sql("SELECT profile_sources_json FROM individuals WHERE individual_id=?", (owner,)).scalar_one())
        assert "name_reason" not in sources[0]
        assert sources[0]["person_id"] == "p0"
    with Session(engine) as session:
        assert len(get_individual_members(session, owner)) == 1
