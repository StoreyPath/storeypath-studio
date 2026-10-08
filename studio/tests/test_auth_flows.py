"""Logging in, sharing and seeing only what is shared, over HTTP: a person who may see
one floor sees that floor alone (its plans, its part of the drawing, a 3D preview
without the others); sharing never goes wider than the sharer's own; a temporary
password is changed before anything else; the first admin is set up once; logins
are throttled; a backup is streamed; Studio without accounts serves this computer
alone."""

import io
import json
import shutil
import tarfile
import threading
import zipfile

import pytest

from sessions import call
from storeypath import accounts as acc
from storeypath.accounts import COOKIE, Accounts, Scope
from storeypath.convert import convert_floor
from storeypath.samples import build_demo, office_floor, write_sheet_dxf
from storeypath.server import Studio, make_server
from storeypath.workspace import SourceDrawing, Workspace

PASSWORD = "everyone's password"


class NoModel:
    name = "none"
    ready = False

    def available(self):
        return False

    def close(self):
        pass


@pytest.fixture(autouse=True)
def quick_hashes(monkeypatch):
    monkeypatch.setattr(acc, "SCRYPT_N", 2**10)
    monkeypatch.delenv("STOREYPATH_ALLOWED_HOSTS", raising=False)


def serve(data, **kw):
    studio = Studio(data, model=NoModel(), warm=False)
    accounts = kw.pop("accounts", None) or Accounts(data)
    srv = make_server(studio, port=0, accounts=accounts, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, studio, accounts


@pytest.fixture
def campus(tmp_path):
    """Studio with two projects: the demo campus (HQ: three floors, each its own drawing;
    Annex: two), and a sheet of two floors side by side (a region each) and a third floor
    read from the whole sheet."""
    data = tmp_path / "data"
    ws_path, packages = build_demo(data / "demo")
    (ws_path.parent / "exports").mkdir()
    for p in packages:
        shutil.copy(p, ws_path.parent / "exports" / p.name)
    ws = Workspace.load(ws_path)
    ws.add_item("DESK-JUNIOR", f"{ws.id}-DEMO-HQ-F00", 2.0, 2.0)
    ws.add_item("SOFA", f"{ws.id}-DEMO-HQ-F01", 2.0, 2.0)
    ws.save(ws_path)
    sheet = Workspace.new("Sheet")
    folder = data / sheet.id
    (folder / "drawings").mkdir(parents=True)
    write_sheet_dxf(folder / "drawings" / "sheet.dxf", [(office_floor(1), (0, 0), "FIRST FLOOR PLAN"),
                                                        (office_floor(2), (100, 0), "SECRET SECOND FLOOR")])
    loc = sheet.add_location("SITE", "Site")
    b = sheet.add_building(loc, "A", "Tower")
    s1 = sheet.add_floor(b, 1, source=SourceDrawing(path="drawings/sheet.dxf", region=(-5000, -5000, 55000, 30000)))
    s2 = sheet.add_floor(b, 2, source=SourceDrawing(path="drawings/sheet.dxf", region=(95000, -5000, 155000, 30000)))
    s3 = sheet.add_floor(b, 3, source=SourceDrawing(path="drawings/sheet.dxf"))  # the whole sheet
    for f in (s1, s2):
        convert_floor(sheet, f, folder)
    sheet.save(folder / f"{sheet.id}.spproj")
    srv, studio, accounts = serve(data)
    users = {name: accounts.add_user(name, PASSWORD, role=role, must_change_password=False)
             for name, role in (("boss", "admin"), ("eng", "engineer"), ("vera", "user"), ("bob", "user"),
                                ("sam", "user"), ("other", "user"))}
    tokens = {n: accounts.start_session(u) for n, u in users.items()}
    hq = f"{ws.id}-DEMO-HQ"
    ids = {"demo": ws.id, "hq": hq, "hq0": f"{hq}-F00", "hq1": f"{hq}-F01", "hq2": f"{hq}-F02",
           "annex": f"{ws.id}-DEMO-ANNEX", "sheet": sheet.id, "s1": s1, "s2": s2, "s3": s3, "packages": packages}
    accounts.set_owner(ws.id, users["eng"].id)
    accounts.set_grant(ws.id, users["vera"].id, Scope(kind="floor", id=ids["hq0"]), "view", users["eng"].id)
    accounts.set_grant(sheet.id, users["vera"].id, Scope(kind="floor", id=s1), "view", users["boss"].id)
    accounts.set_grant(sheet.id, users["vera"].id, Scope(kind="floor", id=s3), "view", users["boss"].id)
    yield srv.server_port, studio, accounts, users, tokens, ids
    srv.shutdown()
    srv.server_close()


# ---- a person who may see one floor ------------------------------------------------------

def test_a_floor_viewer_sees_that_floor_alone(campus):
    port, studio, accounts, users, tokens, ids = campus
    v = tokens["vera"]
    status, projects, _ = call(port, "GET", "/api/projects", token=v)
    assert status == 200 and {p["code"] for p in projects} == {ids["demo"], ids["sheet"]}
    demo = next(p for p in projects if p["code"] == ids["demo"])
    assert demo["floors"] == 1 and demo["can"]["project"] is None and demo["can"]["most"] == "view"
    status, project, _ = call(port, "GET", f"/api/projects/{ids['demo']}", token=v)
    assert status == 200
    buildings = [b for loc in project["locations"] for b in loc["buildings"]]
    assert [b["id"] for b in buildings] == [ids["hq"]]
    assert [f["id"] for f in buildings[0]["floors"]] == [ids["hq0"]]
    assert project["drawings"] == ["hq-level-0.dxf"] and project["exports"] == []
    assert [f["id"] for f in project["floors"]] == [ids["hq0"]] and project["floors"][0]["can"] == "view"
    assert project["can"]["floors"] == {ids["hq0"]: "view"}
    # the building stands where everyone sees it, though drawn from one floor
    _, whole, _ = call(port, "GET", f"/api/projects/{ids['demo']}", token=tokens["boss"])
    everyone = next(b for loc in whole["locations"] for b in loc["buildings"] if b["id"] == ids["hq"])
    assert buildings[0]["site"] == everyone["site"] and buildings[0]["placement"] == everyone["placement"]
    status, review, _ = call(port, "GET", f"/api/projects/{ids['demo']}/review", token=v)
    assert [f["id"] for f in review["floors"]] == [ids["hq0"]]
    for other in (ids["hq1"], f"{ids['annex']}-F00"):
        for tail in ("", "/drawing", "/print", "/print.png"):
            assert call(port, "GET", f"/api/projects/{ids['demo']}/floors/{other}{tail}", token=v)[0] == 404
    status, floor, _ = call(port, "GET", f"/api/projects/{ids['demo']}/floors/{ids['hq0']}", token=v)
    assert status == 200 and [i["type"] for i in floor["items"]] == ["DESK-JUNIOR"]
    # their floor's objects and items may not be changed: only viewed
    space = floor["spaces"][0]["id"]
    assert call(port, "POST", f"/api/projects/{ids['demo']}/objects/{space}", {"hidden": True}, token=v)[0] == 403


def test_a_floor_viewers_preview_holds_their_floors_alone(campus):
    port, studio, accounts, users, tokens, ids = campus
    status, blob, res = call(port, "GET", f"/api/projects/{ids['demo']}/preview.storeypath", token=tokens["vera"])
    assert status == 200 and res.getheader("Content-Type") == "application/zip"
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        floors = json.loads(z.read("floors.geojson"))["features"]
        spaces = json.loads(z.read("spaces.geojson"))["features"]
        items = json.loads(z.read("items.geojson"))["features"]
        buildings = json.loads(z.read("buildings.geojson"))["features"]
        changes = json.loads(z.read("changes.json"))
        manifest = json.loads(z.read("manifest.json"))
    assert [f["id"] for f in floors] == [ids["hq0"]]
    assert spaces and {s["properties"]["floor_id"] for s in spaces} == {ids["hq0"]}
    assert [i["properties"]["floor_id"] for i in items] == [ids["hq0"]]
    assert [b["id"] for b in buildings] == [ids["hq"]]
    assert not changes["retired"] and not changes["all_retired"]  # no IDs of the floors not seen
    assert [s["floor_id"] for s in manifest["sources"]] == [ids["hq0"]]
    # the whole of it to who may see it whole
    _, blob, _ = call(port, "GET", f"/api/projects/{ids['demo']}/preview.storeypath", token=tokens["eng"])
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert len(json.loads(z.read("floors.geojson"))["features"]) == 5
    assert call(port, "GET", f"/api/projects/{ids['demo']}/preview.storeypath?building={ids['annex']}",
                token=tokens["vera"])[0] == 404
    assert call(port, "GET", f"/api/projects/{ids['demo']}/preview.storeypath?building={ids['hq']}",
                token=tokens["vera"])[0] == 200


def test_a_floors_drawing_and_print_are_its_part_of_the_sheet(campus):
    port, studio, accounts, users, tokens, ids = campus
    v, code = tokens["vera"], ids["sheet"]
    status, drawing, _ = call(port, "GET", f"/api/projects/{code}/floors/{ids['s1']}/drawing", token=v)
    assert status == 200 and drawing["texts"]
    texts = " ".join(" ".join(t[4]) for t in drawing["texts"])
    assert "FIRST FLOOR PLAN" in texts and "SECRET" not in texts and "201" not in texts
    _, everything, _ = call(port, "GET", f"/api/projects/{code}/floors/{ids['s2']}/drawing", token=tokens["boss"])
    assert "SECRET" in " ".join(" ".join(t[4]) for t in everything["texts"])
    status, info, _ = call(port, "GET", f"/api/projects/{code}/floors/{ids['s1']}/print", token=v)
    assert status == 200 and info["bounds"] == [-5.0, -5.0, 55.0, 30.0]  # the region, in metres: the print ends there
    assert call(port, "GET", f"/api/projects/{code}/floors/{ids['s1']}/print.png", token=v)[0] == 200
    # a floor read from the whole sheet would show the others: only to who may see them all
    for tail in ("/drawing", "/print", "/print.png"):
        status, said, _ = call(port, "GET", f"/api/projects/{code}/floors/{ids['s3']}{tail}", token=v)
        assert status == 403 and "other floors" in said["error"], tail
    assert call(port, "GET", f"/api/projects/{code}/floors/{ids['s3']}", token=v)[0] == 200  # its plan is fine
    accounts.set_grant(code, users["vera"].id, Scope(kind="floor", id=ids["s2"]), "view", users["boss"].id)
    assert call(port, "GET", f"/api/projects/{code}/floors/{ids['s3']}/drawing", token=v)[0] == 200


def test_packages_need_the_whole_building(campus):
    port, studio, accounts, users, tokens, ids = campus
    hq_pkg, annex_pkg = (p.name for p in ids["packages"])
    base = f"/api/projects/{ids['demo']}/exports"
    assert call(port, "GET", f"{base}/{hq_pkg}", token=tokens["vera"])[0] == 403  # one floor of HQ, not all
    assert call(port, "GET", f"{base}/{annex_pkg}", token=tokens["vera"])[0] == 404
    accounts.set_grant(ids["demo"], users["vera"].id, Scope(kind="building", id=ids["hq"]), "view", users["eng"].id)
    assert call(port, "GET", f"{base}/{hq_pkg}", token=tokens["vera"])[0] == 200
    _, project, _ = call(port, "GET", f"/api/projects/{ids['demo']}", token=tokens["vera"])
    assert project["exports"] == [hq_pkg]
    assert call(port, "POST", f"/api/projects/{ids['demo']}/export", {"building": ids["hq"]},
                token=tokens["vera"])[0] == 403  # viewing is not exporting


def test_items_carried_between_floors_need_both(campus):
    port, studio, accounts, users, tokens, ids = campus
    code, b = ids["demo"], tokens["bob"]
    accounts.set_grant(code, users["bob"].id, Scope(kind="floor", id=ids["hq0"]), "edit", users["eng"].id)
    accounts.set_grant(code, users["bob"].id, Scope(kind="floor", id=ids["hq1"]), "view", users["eng"].id)
    ws = Workspace.load(studio.path(code))
    desk = next(i for i in ws.items.values() if i.floor_id == ids["hq0"])
    sofa = next(i for i in ws.items.values() if i.floor_id == ids["hq1"])
    assert call(port, "POST", f"/api/projects/{code}/items/{desk.id}", {"x": 3}, token=b)[0] == 200
    status, said, _ = call(port, "POST", f"/api/projects/{code}/items/{desk.id}", {"floor_id": ids["hq1"]}, token=b)
    assert status == 403 and "the floor it goes to" in said["error"]
    assert call(port, "POST", f"/api/projects/{code}/items/{desk.id}", {"floor_id": ids["hq2"]}, token=b)[0] == 404
    assert call(port, "POST", f"/api/projects/{code}/items/{sofa.id}", {"x": 3}, token=b)[0] == 403
    accounts.set_grant(code, users["bob"].id, Scope(kind="floor", id=ids["hq1"]), "edit", users["eng"].id)
    assert call(port, "POST", f"/api/projects/{code}/items/{desk.id}", {"floor_id": ids["hq1"]}, token=b)[0] == 200


# ---- sharing ------------------------------------------------------------------------------

def test_sharing_goes_no_wider_than_the_sharers_own(campus):
    port, studio, accounts, users, tokens, ids = campus
    code = ids["demo"]
    owner = tokens["eng"]
    share = f"/api/projects/{code}/access"

    def give(token, user, scope, level):
        return call(port, "POST", share, {"user": users[user].id, "scope": scope, "level": level}, token=token)

    # the owner gives sam share on the Annex
    status, listing, _ = give(owner, "sam", {"kind": "building", "id": ids["annex"]}, "share")
    assert status == 200 and any(g["user"]["username"] == "sam" and g["level"] == "share" for g in listing["grants"])
    s = tokens["sam"]
    assert call(port, "GET", "/api/users", token=s)[0] == 200  # may now pick whom to share with
    status, listing, _ = call(port, "GET", share, token=s)
    assert status == 200
    assert listing["owner"]["username"] == "eng" and listing["scopes"]["project"] is False
    assert [b["id"] for b in listing["scopes"]["buildings"]] == [ids["annex"]]
    # sam sees the grants inside the Annex, not vera's on HQ
    assert {g["user"]["username"] for g in listing["grants"]} == {"sam"}
    # inside the Annex: yes; the project or HQ: no
    assert give(s, "bob", {"kind": "floor", "id": f"{ids['annex']}-F01"}, "edit")[0] == 200
    assert give(s, "bob", {"kind": "building", "id": ids["annex"]}, "view")[0] == 200
    assert give(s, "bob", {"kind": "project"}, "view")[0] == 403
    assert give(s, "bob", {"kind": "building", "id": ids["hq"]}, "view")[0] == 404  # sam sees none of HQ
    # nobody raises their own, nobody touches the owner's
    assert give(s, "sam", {"kind": "project"}, "share")[0] == 403
    assert give(s, "sam", {"kind": "building", "id": ids["annex"]}, None)[0] == 403
    status, said, _ = give(s, "eng", {"kind": "building", "id": ids["annex"]}, "view")
    assert status == 403 and "owner" in said["error"]
    assert give(owner, "eng", {"kind": "project"}, None)[0] == 403
    # a grant taken away; then sam may see the project no more
    assert give(s, "bob", {"kind": "building", "id": ids["annex"]}, None)[0] == 200
    assert give(owner, "sam", {"kind": "building", "id": ids["annex"]}, None)[0] == 200
    assert call(port, "GET", f"/api/projects/{code}", token=s)[0] == 404
    # what was done is in the audit log
    actions = [e["action"] for e in accounts.audit_tail()]
    assert {"grant added", "grant removed"} <= set(actions)
    # only an admin gives a project another owner
    assert call(port, "POST", f"/api/projects/{code}/owner", {"user": users["bob"].id}, token=owner)[0] == 403
    status, listing, _ = call(port, "POST", f"/api/projects/{code}/owner", {"user": users["bob"].id},
                              token=tokens["boss"])
    assert status == 200 and listing["owner"]["username"] == "bob"
    assert call(port, "GET", f"/api/projects/{code}", token=owner)[0] == 404  # eng owned it; no more


def test_projects_belong_to_their_maker_and_go_with_their_access(campus):
    port, studio, accounts, users, tokens, ids = campus
    status, made, _ = call(port, "POST", "/api/projects", {"name": "Mine"}, token=tokens["eng"])
    assert status == 200 and accounts.project_access(made["code"]).owner == users["eng"].id
    assert call(port, "POST", "/api/projects", {"name": "Not mine"}, token=tokens["bob"])[0] == 403
    assert call(port, "GET", f"/api/projects/{made['code']}", token=tokens["bob"])[0] == 404
    assert call(port, "GET", f"/api/projects/{made['code']}", token=tokens["boss"])[0] == 200  # admins see all
    status, _, _ = call(port, "POST", f"/api/projects/{made['code']}/delete", {"confirm": "Mine"}, token=tokens["eng"])
    assert status == 200 and made["code"] not in accounts.all_access()
    # a project from before accounts (no owner): admins manage it, nobody else sees it
    assert accounts.project_access(ids["sheet"]).owner is None
    assert call(port, "GET", f"/api/projects/{ids['sheet']}", token=tokens["eng"])[0] == 404
    assert call(port, "GET", f"/api/projects/{ids['sheet']}/access", token=tokens["boss"])[0] == 200


def test_opening_a_file_needs_the_right_to_what_it_changes(campus, tmp_path):
    port, studio, accounts, users, tokens, ids = campus
    hq_pkg = ids["packages"][0].read_bytes()
    # a package of a building here: edit on that building (and the name typed to replace it)
    status, said, _ = call(port, "PUT", "/api/open", raw=hq_pkg, token=tokens["vera"])
    assert status == 403
    status, said, _ = call(port, "PUT", "/api/open", raw=hq_pkg, token=tokens["other"])
    assert status == 403 and "not shared with you" in said["error"]
    accounts.set_grant(ids["demo"], users["other"].id, Scope(kind="building", id=ids["hq"]), "edit", users["eng"].id)
    status, said, _ = call(port, "PUT", "/api/open", raw=hq_pkg, token=tokens["other"])
    assert status == 409 and said["building"] == ids["hq"]  # theirs to replace, when they type its name
    # a project file in place of the project here: its owner or an admin
    _, project_file, _ = call(port, "GET", f"/api/projects/{ids['demo']}/project.storeypath-project",
                              token=tokens["eng"])
    assert call(port, "PUT", "/api/open?replace=Demo%20Campus", raw=project_file, token=tokens["other"])[0] == 403
    assert call(port, "PUT", "/api/open", raw=project_file, token=tokens["eng"])[0] == 409
    # a new project: admins and engineers, who then own it
    other = Workspace.load(studio.path(ids["demo"])).save_as_new_project(tmp_path / "copy.spproj", "Copy")
    from storeypath.export import export_package

    export_package(other, tmp_path / "copy.storeypath", building=f"{other.id}-DEMO-HQ", record=False)
    new = (tmp_path / "copy.storeypath").read_bytes()
    assert call(port, "PUT", "/api/open", raw=new, token=tokens["bob"])[0] == 403
    status, opened, _ = call(port, "PUT", "/api/open", raw=new, token=tokens["eng"])
    assert status == 200 and accounts.project_access(opened["code"]).owner == users["eng"].id


# ---- logging in ---------------------------------------------------------------------------

def test_logging_in_sets_a_strict_cookie_and_out_clears_it(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        accounts.add_user("ali", PASSWORD, must_change_password=False)
        assert call(port, "GET", "/api/me")[0] == 401
        status, me, res = call(port, "POST", "/api/login", {"username": "ALI", "password": PASSWORD})
        assert status == 200 and me["user"]["username"] == "ali" and me["must_change_password"] is False
        set_cookie = res.getheader("Set-Cookie")
        token = set_cookie.split(";")[0].split("=", 1)[1]
        assert {"HttpOnly", "SameSite=Strict", "Path=/"} <= {p.strip() for p in set_cookie.split(";")}
        assert "Secure" not in set_cookie and len(token) >= 40
        _, again, res2 = call(port, "POST", "/api/login", {"username": "ali", "password": PASSWORD})
        assert res2.getheader("Set-Cookie").split(";")[0] != set_cookie.split(";")[0]  # new every time
        status, who, _ = call(port, "GET", "/api/me", token=token)
        assert status == 200 and who["role"] == "user" and who["create"] is False
        status, _, res = call(port, "POST", "/api/logout", {}, token=token)
        assert status == 200 and "Max-Age=0" in res.getheader("Set-Cookie")
        assert call(port, "GET", "/api/me", token=token)[0] == 401
        status, said, _ = call(port, "POST", "/api/login", {"username": "ali", "password": "wrong password"})
        assert status == 401 and said["error"] == "wrong username or password"
        # logging in is a change: from Studio's own pages only
        assert call(port, "POST", "/api/login", {"username": "ali", "password": PASSWORD},
                    headers={"X-StoreyPath": "0"})[0] == 403
        assert call(port, "POST", "/api/login", {"username": "ali", "password": PASSWORD},
                    headers={"Origin": "https://evil.example"})[0] == 403
        # the pages themselves need no session: they hold no data
        for page in ("/", "/login.html", "/setup.html", "/review.html", "/studio.js"):
            status, _, res = call(port, "GET", page)
            assert status == 200 and res.getheader("X-Frame-Options") == "DENY", page  # never framed
        assert call(port, "GET", "/api/projects")[0] == 401
    finally:
        srv.shutdown()
        srv.server_close()


def test_failed_logins_are_answered_429_with_when_to_try_again(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        accounts.add_user("ali", PASSWORD, must_change_password=False)
        for _ in range(acc.USER_FAILURES):
            assert call(srv.server_port, "POST", "/api/login", {"username": "ali", "password": "nope nope"})[0] == 401
        status, said, res = call(srv.server_port, "POST", "/api/login", {"username": "ali", "password": PASSWORD})
        assert status == 429 and said["retry_after"] > 0 and int(res.getheader("Retry-After")) == said["retry_after"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_temporary_password_is_changed_before_anything_else(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        boss = accounts.add_user("boss", PASSWORD, role="admin", must_change_password=False)
        token = accounts.start_session(boss)
        status, made, _ = call(port, "POST", "/api/admin/users", {"username": "Newbie", "name": "New Bie",
                                                                  "role": "engineer"}, token=token)
        assert status == 200 and made["user"]["must_change_password"] and len(made["password"]) == 19
        status, me, res = call(port, "POST", "/api/login", {"username": "newbie", "password": made["password"]})
        assert status == 200 and me["must_change_password"] is True
        t = res.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        for method, path, body in (("GET", "/api/projects", None), ("GET", "/api/status", None),
                                   ("POST", "/api/projects", {"name": "x"})):
            status, said, _ = call(port, method, path, body, token=t)
            assert status == 403 and said["must_change_password"] is True
        assert call(port, "GET", "/api/me", token=t)[0] == 200
        status, said, _ = call(port, "POST", "/api/me/password", {"current": made["password"], "new": "short"},
                               token=t)
        assert status == 400
        status, me, res = call(port, "POST", "/api/me/password", {"current": made["password"],
                                                                 "new": "my own password now"}, token=t)
        assert status == 200 and me["must_change_password"] is False
        t2 = res.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        assert call(port, "GET", "/api/projects", token=t)[0] == 401  # the old session ended
        assert call(port, "GET", "/api/projects", token=t2)[0] == 200
        # an admin's reset: temporary again, and every session ends
        status, reset, _ = call(port, "POST", f"/api/admin/users/{made['user']['id']}/password", {}, token=token)
        assert status == 200 and call(port, "GET", "/api/me", token=t2)[0] == 401
        # disabled: no login, no session
        _, _, res = call(port, "POST", "/api/login", {"username": "newbie", "password": reset["password"]})
        t3 = res.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        status, user, _ = call(port, "POST", f"/api/admin/users/{made['user']['id']}", {"active": False}, token=token)
        assert status == 200 and user["active"] is False
        assert call(port, "GET", "/api/me", token=t3)[0] == 401
        assert call(port, "POST", "/api/login", {"username": "newbie", "password": reset["password"]})[0] == 401
        status, users, _ = call(port, "GET", "/api/admin/users", token=token)
        assert {"password"} & set(users[0]) == set() and {"role", "active", "last_login_at"} <= set(users[0])
        status, audit, _ = call(port, "GET", "/api/admin/audit", token=token)
        assert {"user created", "password changed", "password reset", "user disabled", "login"} <= \
            {e["action"] for e in audit}
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_first_admin_is_set_up_once_with_the_printed_link(tmp_path):
    srv, studio, accounts = serve(tmp_path / "data")
    try:
        port = srv.server_port
        status, said, _ = call(port, "GET", "/api/me")
        assert status == 401 and said["setup"] is True
        token = accounts.setup_token()
        assert call(port, "POST", "/api/setup", {"token": "guess", "username": "boss", "password": PASSWORD})[0] == 403
        status, me, res = call(port, "POST", "/api/setup", {"token": token, "username": "Boss", "name": "The Boss",
                                                            "password": PASSWORD})
        assert status == 200 and me["user"]["username"] == "boss" and "sp_session=" in res.getheader("Set-Cookie")
        assert call(port, "POST", "/api/setup", {"token": token, "username": "two", "password": PASSWORD})[0] == 404
        assert call(port, "GET", "/api/me")[1]["setup"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_catalogue_or_backup_capability_lets_a_user_do_that(campus):
    port, studio, accounts, users, tokens, ids = campus
    assert call(port, "GET", "/api/backup", token=tokens["bob"])[0] == 403
    accounts.update_user(users["bob"].id, capabilities=["backup", "catalogue"])
    bob = accounts.start_session(accounts.user(users["bob"].id))
    status, blob, res = call(port, "GET", "/api/backup", token=bob)
    assert status == 200
    _, cat, _ = call(port, "GET", "/api/catalogue", token=bob)
    assert call(port, "POST", "/api/catalogue", cat, token=bob)[0] == 200


def test_a_backup_is_streamed_whole_and_recorded(campus):
    port, studio, accounts, users, tokens, ids = campus
    status, blob, res = call(port, "GET", "/api/backup", token=tokens["boss"])
    assert status == 200 and res.getheader("Content-Type") == "application/gzip"
    assert res.getheader("Content-Length") is None and res.getheader("Connection") == "close"
    name = res.getheader("Content-Disposition").split('filename="')[1].rstrip('"')
    assert name.startswith("storeypath-backup-") and name.endswith(".tar.gz")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = set(tar.getnames())
    assert {"storeypath-data/studio.db",
            f"storeypath-data/{ids['sheet']}/{ids['sheet']}.spproj", "storeypath-data/demo/demo.spproj"} <= names
    entry = accounts.audit_tail()[0]
    assert (entry["action"], entry["target"], entry["outcome"], entry["user"]["username"]) == \
        ("backup", name, "ok", "boss")


def test_jobs_are_followed_by_who_may_see_what_they_work_on(campus):
    port, studio, accounts, users, tokens, ids = campus
    accounts.set_grant(ids["demo"], users["bob"].id, Scope(kind="building", id=ids["annex"]), "edit", users["eng"].id)
    status, job, _ = call(port, "POST", f"/api/projects/{ids['demo']}/export", {"building": ids["annex"]},
                          token=tokens["bob"])
    assert status == 200
    kept = studio.jobs.get(job["id"])
    assert (kept.project, kept.scope, kept.user) == (ids["demo"], ("building", ids["annex"]), users["bob"].id)
    assert call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["bob"])[0] == 200
    assert call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["eng"])[0] == 200
    assert call(port, "GET", f"/api/jobs/{job['id']}", token=tokens["vera"])[0] == 404  # HQ's ground floor only
    assert call(port, "GET", "/api/jobs/nope", token=tokens["boss"])[0] == 404


# ---- how Studio is served -----------------------------------------------------------------

def test_accounts_must_be_given_and_none_only_serves_this_computer(tmp_path):
    studio = Studio(tmp_path / "data", model=NoModel(), warm=False)
    with pytest.raises(TypeError):
        make_server(studio, port=0)  # no open server by default
    with pytest.raises(ValueError, match="this computer"):
        make_server(studio, host="0.0.0.0", port=0, accounts=None)
    srv = make_server(studio, port=0, accounts=None)  # storeypath review
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, me, _ = call(srv.server_port, "GET", "/api/me")
        assert status == 200 and me["local"] is True and me["role"] == "admin"
        assert call(srv.server_port, "POST", "/api/projects", {"name": "Here"})[0] == 200
        assert call(srv.server_port, "POST", "/api/login", {"username": "x", "password": "y"})[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
