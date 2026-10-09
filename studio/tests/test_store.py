"""Projects in Studio's database (db/store.py): a project goes in and comes back as it
was; Review's changes are small transactions on what they touch, each with its history
row and a notification; two people changing different things of a floor at once both
keep their change; the schema applies as a plain role that owns its database."""

import json
import os
import threading

import psycopg
import pytest

from conftest import SERVER_URL, _admin, _url
from storeypath import db as studio_db
from storeypath.db.store import ProjectStore
from storeypath.review import Review, StoredProject
from storeypath.workspace import ExportRecord, Override, Reading, Workspace


@pytest.fixture
def stored(converted, tmp_path, databases):
    """The converted sample floor in a database of its own, with a correction, an item,
    a reading, what the vision model saw and an export record."""
    ws, d, f_id, *_ = converted
    space = next(r for r in ws.floor_objects(f_id) if r.kind == "space")
    ws.overrides[space.id] = Override(name="Boardroom")
    ws.add_item("DESK-JUNIOR", f_id, 2.5, 3.0, 90)
    ws.readings["MEETING"] = Reading(type="meeting_room", source="rules")
    ws.vision["abc"] = {"type": "office", "sure": 0.8}
    ws.exports.append(ExportRecord(sequence=1, exported_at=ws.project.created_at, file="x.storeypath",
                                   objects={space.id: "0123456789abcdef"}))
    store = ProjectStore(studio_db.connect(data=tmp_path / "data"))
    store.create(ws)
    return store, ws, f_id, space.id


def test_a_project_comes_back_as_it_went_in(stored):
    store, ws, f_id, _ = stored
    store.forget()  # read from the database, not from memory
    back = store.current(ws.id)
    as_a_file = Workspace.model_validate_json(ws.model_dump_json())  # (its shapes' points as lists)
    assert back == as_a_file and back.model_dump(mode="json") == ws.model_dump(mode="json")
    mine = store.load(ws.id)
    assert mine == as_a_file and mine is not back  # one's own, to change
    assert store.save_project(mine) is False  # nothing differs: nothing written
    floor = store.load(ws.id, floors=[f_id])
    assert set(floor.objects) == {i for i in ws.objects if i.startswith(f_id + "-")}
    with pytest.raises(ValueError, match="part of the project"):
        store.save_project(floor)  # holds one floor: saves that floor alone
    store.save_floor(floor, f_id)


def test_a_change_writes_what_it_touches_with_its_history_and_a_notification(stored, tmp_path):
    store, ws, f_id, space = stored
    listener = psycopg.connect(store.db.url, autocommit=True)
    listener.execute("LISTEN storeypath_changes")
    before = store.version(ws.id)
    with store.db.connection() as conn:
        floor_version = conn.execute("SELECT version FROM floors WHERE id = %s", (f_id,)).fetchone()[0]
    review = Review(StoredProject(store, ws.id, tmp_path / "cache"))
    out = review.correct(space, {"correction": {"type": "office", "name": "Room 1"}}, by="U0000000001")
    assert out["type"] == "office" and out["name"] == "Room 1"
    note = next(listener.notifies(timeout=5, stop_after=1))
    payload = json.loads(note.payload)
    assert payload["project"] == ws.id and payload["floors"] == [f_id] and payload["version"] == before + 1
    entry = store.history(ws.id)[0]
    assert entry["seq"] == payload["seq"] and entry["version"] == before + 1
    assert (entry["part"], entry["kind"], entry["targets"], entry["floors"]) == ("object", "correct", [space], [f_id])
    assert entry["before"] == {"override": {"name": "Boardroom"}}
    assert entry["after"] == {"override": {"type": "office", "name": "Room 1"}}
    assert entry["who"] == {"id": "U0000000001"}  # (no such user: kept as it was said)
    with store.db.connection() as conn:
        row = conn.execute("SELECT name, changed_by, version FROM overrides WHERE object = %s", (space,)).fetchone()
        assert conn.execute("SELECT version FROM floors WHERE id = %s", (f_id,)).fetchone()[0] == floor_version + 1
    assert row == ("Room 1", "U0000000001", 2)
    # the project in memory moved with it, without being read again
    assert store._kept[ws.id].version == before + 1 and store.current(ws.id).overrides[space].name == "Room 1"
    # a change of nothing records nothing
    review.correct(space, {"correction": {"type": "office", "name": "Room 1"}})
    assert store.version(ws.id) == before + 1 and len(store.history(ws.id)) == 2  # (and the project made)
    # an item placed, and a wall drawn: each its own row and history
    item = review.add_item(f_id, {"type": "SOFA", "x": 4, "y": 4})
    review.edit(f_id, {"add": {"wall": [[0, 0], [3, 0]]}})
    kinds = [(e["part"], e["kind"]) for e in store.history(ws.id, floor=f_id)]
    assert kinds[:2] == [("edit", "draw"), ("item", "add")]
    edit = store.history(ws.id)[0]
    assert edit["list"] == "walls" and edit["after"] == [[[0.0, 0.0], [3.0, 0.0]]] and edit["before"] == []
    store.forget()
    again = store.current(ws.id)
    assert again.items[item["id"]].type == "SOFA" and again.floor(f_id).edits.walls == [[[0.0, 0.0], [3.0, 0.0]]]
    listener.close()


def test_two_people_changing_a_floor_at_once_both_keep_their_change(stored, tmp_path):
    store, ws, f_id, _ = stored
    other = ProjectStore(studio_db.Database(store.db.url))  # another Studio's connections on the same database
    spaces = sorted(r.id for r in ws.floor_objects(f_id) if r.kind == "space")[:8]
    reviews = [Review(StoredProject(s, ws.id, tmp_path / "cache")) for s in (store, other)]
    errors = []

    def name(review, ids, who):
        try:
            for i in ids:
                review.correct(i, {"correction": {"name": f"{who} {i[-4:]}"}})
        except Exception as e:  # noqa: BLE001 (reported below)
            errors.append(e)

    threads = [threading.Thread(target=name, args=(r, spaces[k::2], f"by {k}")) for k, r in enumerate(reviews)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    for s in (store, other):
        s.forget()
        named = {i: s.current(ws.id).overrides[i].name for i in spaces}
        assert named == {i: f"by {k % 2} {i[-4:]}" for k, i in enumerate(spaces)}
    assert len(store.history(ws.id, limit=100)) == len(spaces) + 1
    other.db.close()


def test_an_item_id_is_one_projects_in_the_whole_studio(stored, tmp_path, monkeypatch):
    # An item's ID is the key of its row in the whole Studio (no project counts its
    # items): a new item's is drawn again while an item of any project here has it, and
    # an item of another project is never written over (an asset is one project's).
    from storeypath import ids
    from storeypath.errors import Conflict

    store, ws, f_id, _ = stored
    (desk,) = ws.items.values()
    copy = ws.save_as_new_project(tmp_path / "copy.spproj", "Copy")  # another project, its items its own
    store.create(copy)
    (theirs,) = copy.items
    assert store.item_taken(theirs) and store.item_taken(desk.id) and not store.item_taken("ZZZZ-ZZZZ-ZZA")
    symbols = iter("".join(i.replace("-", "")[:10] for i in (theirs, desk.id, "ZZZZ-ZZZZ-ZZA")))
    monkeypatch.setattr(ids.secrets, "choice", lambda alphabet: next(symbols))
    review = Review(StoredProject(store, ws.id, tmp_path / "cache"))
    added = review.add_item(f_id, {"type": "COPIER", "x": 1.0, "y": 1.0})
    assert added["id"] == "ZZZZ-ZZZZ-ZZA" and next(symbols, None) is None
    monkeypatch.undo()
    with store.db.connection() as conn:
        rows = conn.execute("SELECT project, id FROM items ORDER BY position").fetchall()
        assert rows == [(ws.id, desk.id), (copy.id, theirs), (ws.id, "ZZZZ-ZZZZ-ZZA")]
        assert not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'projects' "
                                "AND column_name = 'next_item_seq'").fetchone()  # no project counts its items

    # a third project whose item has the first's desk's ID: refused, nothing made
    third = copy.save_as_new_project(tmp_path / "third.spproj", "Third")
    (mine,) = third.items.values()
    third.items = {desk.id: mine.model_copy(update={"id": desk.id})}
    with pytest.raises(Conflict, match=f"item {desk.id} is an item of another project here"):
        store.create(third)
    assert not store.exists(third.id) and store.current(ws.id).items[desk.id] == desk


def test_the_schema_applies_as_a_plain_role_owning_its_database(databases):
    # As in the studio image: Studio's role owns its database and is no superuser; the
    # image made PostGIS before Studio started.
    role, name = f"spa_{os.getpid()}_role", f"spa_{os.getpid()}_plain"
    with _admin() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'DROP ROLE IF EXISTS "{role}"')
        conn.execute(f"CREATE ROLE \"{role}\" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'plain'")
        conn.execute(f'CREATE DATABASE "{name}" OWNER "{role}"')
    try:
        with psycopg.connect(_url(name), autocommit=True) as conn:
            conn.execute("CREATE EXTENSION postgis")
        url = psycopg.conninfo.make_conninfo(SERVER_URL, dbname=name, user=role, password="plain")
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone() == (False,)
        assert studio_db.migrate(url) == [n for _, n, _ in studio_db.migrations()]
        db = studio_db.Database(url)
        try:
            store = ProjectStore(db)
            ws = Workspace.new("Plain")
            ws.add_location("SITE", "Site")
            store.create(ws)
            assert store.current(ws.id) == ws
        finally:
            db.close()
    finally:
        with _admin() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'DROP ROLE IF EXISTS "{role}"')
