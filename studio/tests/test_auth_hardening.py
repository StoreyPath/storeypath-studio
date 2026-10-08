"""What a person may not get round, over HTTP: a file opened changes only the project
it names, all through; logins and password checks cannot be used to fill Studio up,
lock someone out or run it out of memory; connections cannot be held open for ever;
a backup carries no certificate key and no session; nothing tells a person more than
they may see."""

import io
import json
import zipfile

from sessions import call
from storeypath.bundle import export_project
from storeypath.workspace import Project, Workspace
from test_auth_flows import PASSWORD, campus, quick_hashes, serve  # noqa: F401 (fixtures)


def rezip(blob: bytes, changes: dict) -> bytes:
    """A ZIP with some of its files changed: name -> its new bytes, a function of its
    JSON (the new JSON), or None to leave it out; a name it lacks is added."""
    src = zipfile.ZipFile(io.BytesIO(blob))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            if info.filename not in changes:
                z.writestr(info, src.read(info))
        for name, value in changes.items():
            if value is None:
                continue
            if callable(value):
                value = json.dumps(value(json.loads(src.read(name)))).encode()
            z.writestr(name, value)
    return out.getvalue()


def engineer(accounts, name="mallory"):
    """An engineer nothing is shared with, and their session."""
    user = accounts.add_user(name, PASSWORD, role="engineer", must_change_password=False)
    return user, accounts.start_session(user)


# ---- a file opened ---------------------------------------------------------------------

def test_a_file_whose_parts_name_different_projects_is_refused(campus):
    port, studio, accounts, users, tokens, ids = campus
    mallory, m = engineer(accounts)
    demo = ids["demo"]
    before = studio.path(demo).read_bytes()
    _, project_file, _ = call(port, "GET", f"/api/projects/{demo}/project.storeypath-project", token=tokens["boss"])
    new_code = lambda about: {**about, "project": {**about["project"], "id": "ZZZZ0000"}}  # noqa: E731
    forged = {
        # a project file: its project.json names a new project, its workspace the demo
        "project file": rezip(project_file, {"project.json": new_code}),
        # a package of HQ, with a project.json naming a new project
        "package": rezip(ids["packages"][0].read_bytes(), {"project.json": json.dumps(
            {"format": "storeypath-project", "project": {"id": "ZZZZ0000", "name": "Mine"}}).encode()}),
    }
    for what, blob in forged.items():
        for query in ("", "?replace=Demo%20Campus"):
            status, said, _ = call(port, "PUT", f"/api/open{query}", raw=blob, token=m)
            assert status == 400 and "different projects" in said["error"], (what, query, status, said)
            assert "Demo Campus" not in json.dumps(said) and demo not in json.dumps(said), (what, said)
    assert studio.path(demo).read_bytes() == before
    assert accounts.project_access(demo).owner == users["eng"].id
    assert "ZZZZ0000" not in accounts.all_access()


def test_a_new_project_never_takes_the_place_of_a_folder_that_is_not_its_own(campus, tmp_path):
    port, studio, accounts, users, tokens, ids = campus
    mallory, m = engineer(accounts)
    # a project kept in a folder not named by its code (moved there by hand)
    kept = Workspace.new("Kept by hand")
    kept.add_location("SITE", "Site")
    (studio.data / "QQQQ1111").mkdir()
    kept.save(studio.data / "QQQQ1111" / f"{kept.id}.spproj")
    # a new project whose code is that folder's name
    fresh = Workspace.new("Fresh")
    fresh.project = Project(code="QQQQ1111", name="Fresh")
    (tmp_path / "out" / "QQQQ1111").mkdir(parents=True)
    fresh.save(tmp_path / "out" / "QQQQ1111" / "QQQQ1111.spproj")
    buf = io.BytesIO()
    export_project(tmp_path / "out" / "QQQQ1111" / "QQQQ1111.spproj", buf)
    for query in ("", "?replace=Fresh"):
        status, said, _ = call(port, "PUT", f"/api/open{query}", raw=buf.getvalue(), token=m)
        assert status == 400 and "not this project" in said["error"], (query, status, said)
    assert studio.path(kept.id).parent.name == "QQQQ1111"  # still there, as it was
    assert accounts.project_access("QQQQ1111").owner is None


def test_a_new_projects_owner_is_never_put_in_place_of_one_kept(campus, tmp_path):
    port, studio, accounts, users, tokens, ids = campus
    mallory, m = engineer(accounts)
    # who a project of this code was shared with is kept (its folder was removed by hand)
    accounts.set_owner("QQQQ2222", users["eng"].id)
    fresh = Workspace.new("Fresh")
    fresh.project = Project(code="QQQQ2222", name="Fresh")
    (tmp_path / "out" / "QQQQ2222").mkdir(parents=True)
    fresh.save(tmp_path / "out" / "QQQQ2222" / "QQQQ2222.spproj")
    buf = io.BytesIO()
    export_project(tmp_path / "out" / "QQQQ2222" / "QQQQ2222.spproj", buf)
    status, said, _ = call(port, "PUT", "/api/open", raw=buf.getvalue(), token=m)
    assert status == 403 and "from before" in said["error"], (status, said)
    assert accounts.project_access("QQQQ2222").owner == users["eng"].id
    assert "QQQQ2222" not in studio._workspaces()  # nothing was opened
    # an admin opens it; who it is shared with stays as it was
    status, opened, _ = call(port, "PUT", "/api/open", raw=buf.getvalue(), token=tokens["boss"])
    assert status == 200 and opened["code"] == "QQQQ2222"
    assert accounts.project_access("QQQQ2222").owner == users["eng"].id
