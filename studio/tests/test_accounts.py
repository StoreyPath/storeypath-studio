"""Accounts, sessions and sharing (accounts.py): passwords kept as scrypt hashes in
studio.db (WAL, a versioned schema, written by Studio and the command line at once),
sessions kept by the hash of their token and ended when they should, logins
throttled, and each person's level on a project, building and floor."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading

import pytest

from storeypath import accounts as acc
from storeypath.accounts import (Accounts, Forbidden, Gone, ProjectAccess, Scope, Sight, Throttled, Unauthorized,
                                 check_password, check_username, hash_password, temporary_password, verify_password)
from storeypath.workspace import Workspace

PASSWORD = "a long enough one"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---- passwords and usernames -----------------------------------------------------

def test_a_password_is_kept_as_an_scrypt_hash_and_checked_against_it():
    stored = hash_password("correct horse battery")
    kind, n, r, p, salt, key = stored.split("$")
    assert (kind, n, r, p) == ("scrypt", str(2**15), "8", "1")
    assert "correct" not in stored
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batter", stored)
    assert hash_password("correct horse battery") != stored  # a salt of its own each time
    for broken in ("", "scrypt$1$2", "bcrypt$1$1$1$AA==$AA==", "scrypt$x$8$1$AA==$AA=="):
        assert not verify_password("anything", broken)


def test_usernames_are_lower_case_and_never_paths():
    assert check_username(" Khalefa.A ") == "khalefa.a"
    for bad in ("a", "-x", ".x", "../etc", "a/b", "a b", "x" * 33, "", None, 5, "al\ni", "عل"):
        with pytest.raises(ValueError):
            check_username(bad)


def test_passwords_are_ten_characters_and_not_the_username():
    assert check_password("0123456789", "ali") == "0123456789"
    for bad in ("short", "", None, "x" * 1025):
        with pytest.raises(ValueError):
            check_password(bad, "ali")
    with pytest.raises(ValueError, match="not the username"):
        check_password("LongUserName", "longusername")
    t = temporary_password()
    assert len(t) == 19 and check_password(t, "x")


# ---- the database -------------------------------------------------------------------

def test_accounts_are_kept_in_a_private_sqlite_file(tmp_path):
    a = Accounts(tmp_path)
    user = a.add_user("ali", PASSWORD, role="engineer")
    db = tmp_path / "studio.db"
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    with sqlite3.connect(db) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert raw.execute("PRAGMA user_version").fetchone()[0] == len(acc.MIGRATIONS)
        (stored,) = raw.execute("SELECT password FROM users WHERE username = 'ali'").fetchone()
    assert stored.startswith("scrypt$") and PASSWORD.encode() not in db.read_bytes()
    with pytest.raises(ValueError, match="already"):
        a.add_user("ALI", PASSWORD)  # unique whatever the case
    assert a.by_username("Ali").id == user.id and user.id.startswith("U") and len(user.id) == 11
    assert Accounts(tmp_path).user(user.id) == user  # opened again: brought up to date once, nothing lost


def test_a_database_of_a_newer_studio_is_not_opened(tmp_path):
    Accounts(tmp_path)
    with sqlite3.connect(tmp_path / "studio.db") as raw:
        raw.execute(f"PRAGMA user_version={len(acc.MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="newer Studio"):
        Accounts(tmp_path)


def test_sessions_are_kept_by_the_hash_of_their_token_and_outlive_a_restart(tmp_path):
    a = Accounts(tmp_path)
    user = a.add_user("ali", PASSWORD, must_change_password=False)
    token, _ = a.login("ali", PASSWORD, "10.0.0.7")
    with sqlite3.connect(tmp_path / "studio.db") as raw:
        rows = raw.execute("SELECT token_hash, user, address FROM sessions").fetchall()
    assert rows == [(acc._token_hash(token), user.id, "10.0.0.7")] and token not in rows[0][0]
    assert Accounts(tmp_path).session(token).id == user.id  # Studio started again: still logged in


def test_writes_from_many_threads_and_another_process_are_all_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)  # quicker: what is tested is the lock
    a = Accounts(tmp_path)
    threads = [threading.Thread(target=a.add_user, args=(f"user{i}", PASSWORD)) for i in range(12)]
    code = ("import sys; from storeypath import accounts as acc; acc.SCRYPT_N = 2**10; "
            f"a = acc.Accounts({str(tmp_path)!r}); [a.add_user(f'proc{{i}}', {PASSWORD!r}) for i in range(8)]")
    proc = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert proc.wait(timeout=60) == 0
    names = {u.username for u in Accounts(tmp_path).users()}
    assert names == {f"user{i}" for i in range(12)} | {f"proc{i}" for i in range(8)}


def test_a_change_on_disk_is_read_again(tmp_path):
    studio, cli = Accounts(tmp_path), Accounts(tmp_path)  # Studio running, and the command line
    assert not studio.has_users()
    cli.add_user("boss", PASSWORD, role="admin", must_change_password=False)
    cli.add_user("ali", PASSWORD, role="admin", must_change_password=False)
    assert studio.by_username("ali") is not None
    user = studio.by_username("ali")
    token = studio.start_session(user)
    cli.update_user(user.id, active=False)  # `storeypath users disable ali`
    assert studio.session(token) is None


def test_the_audit_log_says_who_did_what_from_where(tmp_path):
    a = Accounts(tmp_path)
    user = a.add_user("ali", PASSWORD, role="admin", must_change_password=False)
    a.audit("backup", user, "10.0.0.5", "storeypath-backup-x.tar.gz")
    a.audit("login", None, "10.0.0.6", target="nobody", outcome="failed")
    a.audit("grant added", user, "10.0.0.5", "K7Q2XM", who="vera", level="view")
    newest, second, first = a.audit_tail()
    assert first["user"] == {"id": user.id, "username": "ali"} and first["action"] == "backup"
    assert set(first) >= {"at", "user", "address", "action", "target", "outcome"}
    assert (second["action"], second["user"], second["outcome"]) == ("login", None, "failed")
    assert (newest["who"], newest["level"]) == ("vera", "view")  # what more was said of it
    assert [e["action"] for e in a.audit_tail(1)] == ["grant added"]


# ---- sessions and logging in --------------------------------------------------------------

def test_sessions_end_when_idle_too_long_after_a_week_and_on_logout(tmp_path):
    clock = Clock()
    a = Accounts(tmp_path, clock=clock)
    user = a.add_user("ali", PASSWORD, must_change_password=False)
    token, who = a.login("Ali", PASSWORD, "1.2.3.4")
    assert who.id == user.id and a.session(token).id == user.id
    clock.t += acc.IDLE_S - 60
    assert a.session(token) is not None  # used: idle again from now
    clock.t += acc.IDLE_S + 1
    assert a.session(token) is None
    token, _ = a.login("ali", PASSWORD)
    for _ in range(int(acc.ABSOLUTE_S / acc.IDLE_S) + 1):  # used all the time, still ended after a week
        clock.t += acc.IDLE_S - 60
        a.session(token)
    assert a.session(token) is None
    token, _ = a.login("ali", PASSWORD)
    assert a.logout(token).id == user.id and a.session(token) is None
    assert a.session(None) is None and a.session("made-up") is None
    assert a.login("ali", PASSWORD)[0] != a.login("ali", PASSWORD)[0]  # a new token every login


def test_a_password_role_or_capability_changed_ends_every_session(tmp_path):
    a = Accounts(tmp_path)
    admin = a.add_user("boss", PASSWORD, role="admin", must_change_password=False)
    user = a.add_user("ali", PASSWORD, must_change_password=False)
    t1, t2 = a.start_session(user), a.start_session(user)
    a.update_user(user.id, name="Ali A.")  # a name is not who they are
    assert a.session(t1) and a.session(t2)
    a.update_user(user.id, capabilities=["backup"])
    assert a.session(t1) is None and a.session(t2) is None
    t1 = a.start_session(a.user(user.id))
    a.update_user(user.id, role="engineer")
    assert a.session(t1) is None
    t1, t2 = a.start_session(a.user(user.id)), a.start_session(a.user(user.id))
    t3 = a.change_own_password(a.user(user.id), PASSWORD, "another long one")
    assert a.session(t1) is None and a.session(t2) is None and a.session(t3).id == user.id
    with pytest.raises(Forbidden):
        a.change_own_password(a.user(user.id), "wrong one!!", "a third long one")
    with pytest.raises(ValueError, match="no admin|an admin"):
        a.update_user(admin.id, active=False)  # the last admin who can log in
    a.update_user(user.id, role="admin")
    a.update_user(admin.id, active=False)
    with pytest.raises(Unauthorized):
        a.login("boss", PASSWORD)


def test_an_unknown_user_and_a_wrong_password_look_alike(tmp_path):
    a = Accounts(tmp_path)
    a.add_user("ali", PASSWORD)
    with pytest.raises(Unauthorized) as unknown:
        a.login("nobody", PASSWORD)
    with pytest.raises(Unauthorized) as wrong:
        a.login("ali", "not the password")
    assert str(unknown.value) == str(wrong.value) == "wrong username or password"
    assert a._dummy is not None  # a password was checked for the unknown user too
    outcomes = [(e["target"], e["outcome"]) for e in a.audit_tail()]
    assert outcomes == [("ali", "failed"), ("nobody", "failed")]


def test_failed_logins_are_throttled_by_username_and_by_address(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    clock = Clock()
    a = Accounts(tmp_path, clock=clock)
    a.add_user("ali", PASSWORD, must_change_password=False)
    for _ in range(acc.USER_FAILURES):
        with pytest.raises(Unauthorized):
            a.login("ali", "wrong wrong", "1.1.1.1")
    with pytest.raises(Throttled) as e:
        a.login("ali", PASSWORD, "1.1.1.1")  # even the right password, from there: it waits
    assert e.value.more["retry_after"] == acc.THROTTLE_FIRST_S
    assert a.login("ali", PASSWORD, "2.2.2.2")[1].username == "ali"  # from elsewhere: never locked out
    clock.t += acc.THROTTLE_FIRST_S + 1
    with pytest.raises(Unauthorized):
        a.login("ali", "wrong again", "1.1.1.1")
    with pytest.raises(Throttled) as e:
        a.login("ali", PASSWORD, "1.1.1.1")
    assert e.value.more["retry_after"] == 2 * acc.THROTTLE_FIRST_S  # doubling…
    clock.t += acc.THROTTLE_WINDOW_S + 1  # …until the failures are old
    assert a.login("ali", PASSWORD, "1.1.1.1")[1].username == "ali"
    # one address trying many usernames
    for i in range(acc.ADDRESS_FAILURES):
        with pytest.raises(Unauthorized):
            a.login(f"guess{i}", "wrong wrong", "6.6.6.6")
    with pytest.raises(Throttled):
        a.login("ali", PASSWORD, "6.6.6.6")
    assert a.login("ali", PASSWORD, "7.7.7.7")[1].username == "ali"
    assert any(e["outcome"] == "throttled" for e in a.audit_tail())
    zed = ("user", "zed", "8.8.8.8")
    a._failures[zed] = [clock.t] * 40  # never more than the most
    assert a._waiting([zed], clock.t)[0] <= acc.THROTTLE_MAX_S


def test_the_setup_token_makes_the_first_admin_once(tmp_path):
    a = Accounts(tmp_path)
    token = a.setup_token()
    assert token and a.setup_token() == token  # once per start
    with pytest.raises(Forbidden):
        a.setup("not it", "boss", "Boss", PASSWORD)
    session, user = a.setup(token, "Boss", "The Boss", PASSWORD)
    assert user.role == "admin" and not user.must_change_password and a.session(session).id == user.id
    assert a.setup_token() is None
    with pytest.raises(Gone):
        a.setup(token, "again", "Again", PASSWORD)


def test_an_admin_from_the_environment_when_there_are_no_users(tmp_path):
    a = Accounts(tmp_path)
    assert a.bootstrap({}) is None
    with pytest.raises(ValueError):
        a.bootstrap({"STOREYPATH_ADMIN_PASSWORD": "short"})
    user = a.bootstrap({"STOREYPATH_ADMIN_PASSWORD": PASSWORD, "STOREYPATH_ADMIN_USER": "Root"})
    assert (user.username, user.role, user.must_change_password) == ("root", "admin", False)
    assert a.bootstrap({"STOREYPATH_ADMIN_PASSWORD": "another password"}) is None  # only the first time
    assert a.setup_token() is None


# ---- sharing: who may do what where -------------------------------------------------------

def campus():
    """A project of two buildings: A with floors 0 and 1, B with floor 0."""
    ws = Workspace.new("Campus")
    loc = ws.add_location("SITE", "Site")
    a = ws.add_building(loc, "A", "Tower A")
    b = ws.add_building(loc, "B", "Tower B")
    floors = [ws.add_floor(a, 0), ws.add_floor(a, 1), ws.add_floor(b, 0)]
    return ws, a, b, floors


def user(uid="U1", role="user"):
    return acc.User(id=uid, username=uid.lower(), role=role, password="!")


def grant(uid, kind, level, sid=None):
    return acc.Grant(user=uid, scope=Scope(kind=kind, id=sid), level=level)


def test_a_level_is_the_highest_of_the_floors_its_buildings_and_the_projects():
    ws, a, b, (a0, a1, b0) = campus()
    access = ProjectAccess(owner="OWNER", grants=[grant("U1", "floor", "edit", a0), grant("U1", "building", "view", a),
                                                  grant("U2", "project", "view"), grant("U2", "floor", "share", b0)])
    s = Sight(ws, access, user("U1"))
    assert (s.project, s.building(a), s.building(b)) == (None, "view", None)
    assert (s.floor(a0), s.floor(a1), s.floor(b0)) == ("edit", "view", None)
    assert s.sees_building(a) and not s.sees_building(b) and s.any() and not s.whole
    s2 = Sight(ws, access, user("U2"))
    assert (s2.project, s2.floor(a0), s2.floor(b0)) == ("view", "view", "share") and s2.whole
    owner = Sight(ws, access, user("OWNER"))
    assert owner.owner and owner.project == "share" and owner.floor(b0) == "share"
    admin = Sight(ws, access, user("U9", "admin"))
    assert admin.admin and admin.project == "share"
    nobody = Sight(ws, access, user("U3"))
    assert not nobody.any() and nobody.most() is None
    assert Sight(ws, None, None).project == "share"  # no accounts: everything


def test_grants_cover_floors_and_buildings_added_later():
    ws, a, b, (a0, a1, b0) = campus()
    access = ProjectAccess(grants=[grant("U1", "building", "edit", a), grant("U2", "project", "view")])
    a2 = ws.add_floor(a, 2)
    c = ws.add_building(f"{ws.id}-SITE", "C", "Annex")
    c0 = ws.add_floor(c, 0)
    s1, s2 = Sight(ws, access, user("U1")), Sight(ws, access, user("U2"))
    assert s1.floor(a2) == "edit" and s1.floor(c0) is None
    assert s2.floor(a2) == s2.floor(c0) == "view"


def test_a_building_grant_is_not_given_by_how_ids_begin():
    ws = Workspace.new("Ids")
    loc = ws.add_location("SITE", "Site")
    b1 = ws.add_building(loc, "B1", "One")
    b10 = ws.add_building(loc, "B10", "Ten")  # its ID begins with B1's
    f10 = ws.add_floor(b10, 0)
    s = Sight(ws, ProjectAccess(grants=[grant("U1", "building", "edit", b1)]), user("U1"))
    assert s.floor(f10) is None and not s.sees_building(b10)


def test_what_is_seen_of_a_project_leaves_the_other_floors_out(converted_campus):
    ws, a0, a1, b0 = converted_campus
    s = Sight(ws, ProjectAccess(grants=[grant("U1", "floor", "view", a1)]), user("U1"))
    seen = s.seen(ws)
    assert [fid for *_, fid in seen.iter_floors()] == [a1]
    assert {i.rsplit("-", 1)[0] for i in seen.objects} == {a1}
    assert all(it.floor_id == a1 for it in seen.items.values())
    assert set(seen.overrides) <= set(seen.objects)
    assert seen.exports == [] and seen.readings == {} and seen.vision == {}
    assert [fid for *_, fid in ws.iter_floors()] == [a0, a1, b0]  # the project itself as it was
    whole = Sight(ws, ProjectAccess(grants=[grant("U1", "project", "view")]), user("U1"))
    assert whole.seen(ws) is ws


@pytest.fixture
def converted_campus(tmp_path):
    from storeypath.convert import convert_floor
    from storeypath.samples import office_floor, write_floor_dxf
    from storeypath.workspace import SourceDrawing

    write_floor_dxf(tmp_path / "plan.dxf", office_floor(1))
    ws = Workspace.new("Campus")
    loc = ws.add_location("SITE", "Site")
    a = ws.add_building(loc, "A", "Tower A")
    b = ws.add_building(loc, "B", "Tower B")
    a0 = ws.add_floor(a, 0, source=SourceDrawing(path="plan.dxf"))
    a1 = ws.add_floor(a, 1, source=SourceDrawing(path="plan.dxf"))
    b0 = ws.add_floor(b, 0, source=SourceDrawing(path="plan.dxf"))
    for f in (a0, a1, b0):
        convert_floor(ws, f, tmp_path)
        ws.add_item("DESK-JUNIOR", f, 1.0, 1.0)
    first = next(iter(ws.objects))
    ws.overrides[first] = acc_override()
    return ws, a0, a1, b0


def acc_override():
    from storeypath.workspace import Override

    return Override(name="Kept")
