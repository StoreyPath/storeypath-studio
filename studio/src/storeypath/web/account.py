"""The calls of the person's own account, the item types, the users (admins), the
audit log, backups and the GPU helpers (admins). (Every POST takes a JSON object, used
or not, as it always has: anything else is answered 400.)"""

from __future__ import annotations

from fastapi import Request

from .calls import Body, Calls, May, Query, TheStudio, answer

calls = Calls()


# ---- logging in and out, and the person's own -------------------------------------------

@calls.post("/api/login")
def login(request: Request, may: May, body: Body):
    may.anyone()
    return answer(request, may.login(body))


@calls.post("/api/logout")
def logout(request: Request, may: May, body: Body):
    may.anyone()
    return answer(request, may.logout())


@calls.get("/api/me")
def me(request: Request, may: May):
    may.me()
    return answer(request, may.whoami())


@calls.post("/api/me/password")
def own_password(request: Request, may: May, body: Body):
    may.me()
    return answer(request, may.change_password(body))


@calls.get("/api/status")
def status(request: Request, may: May, studio: TheStudio):
    user = may.logged_in()
    return answer(request, studio.status(paths=user.role == "admin"))


@calls.get("/api/catalogue")
def catalogue(request: Request, may: May, studio: TheStudio):
    may.logged_in()
    return answer(request, studio.catalogue().model_dump())


@calls.post("/api/catalogue")
def save_catalogue(request: Request, may: May, studio: TheStudio, body: Body):
    may.capability("catalogue")
    return answer(request, studio.save_catalogue(body))


@calls.get("/api/users")
def users_to_share(request: Request, may: May):
    may.picker()
    return answer(request, may.users_to_share())


# ---- users (admins), the audit log and backups -------------------------------------------

@calls.get("/api/admin/users")
def all_users(request: Request, may: May):
    may.admin()
    return answer(request, may.all_users())


@calls.post("/api/admin/users")
def add_user(request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.add_user(body))


@calls.post("/api/admin/users/{user_id}")
def change_user(user_id: str, request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.change_user(user_id, body))


@calls.post("/api/admin/users/{user_id}/password")
def reset_password(user_id: str, request: Request, may: May, body: Body):
    may.admin()
    return answer(request, may.reset_password(user_id))


@calls.get("/api/admin/audit")
def audit(request: Request, may: May, query: Query):
    may.admin()
    return answer(request, may.audit_log(query))


@calls.get("/api/backup")
def backup(request: Request, may: May):
    may.capability("backup")
    return answer(request, may.backup())


# ---- the GPU helpers (admins): the vision model's servers ---------------------------------

@calls.get("/api/admin/helpers")
def helpers(request: Request, may: May, studio: TheStudio):
    may.admin()
    return answer(request, studio.helpers())


@calls.post("/api/admin/helpers")
def save_helpers(request: Request, may: May, studio: TheStudio, body: Body):
    may.admin()
    saved = studio.save_helpers(body)
    may.audit("gpu helpers changed", None, helpers=[h["url"] for h in saved["helpers"]])
    return answer(request, saved)


@calls.post("/api/admin/helpers/test")
def test_helper(request: Request, may: May, studio: TheStudio, body: Body):
    may.admin()
    return answer(request, studio.test_helper(body))
