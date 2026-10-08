"""Finding the way in a building (navigation.py): its walking network as it is now, or
the way on it from a place to a place, for whoever may see the whole building."""

from __future__ import annotations

from fastapi import Request

from .calls import Calls, May, Query, TheStudio, answer

calls = Calls()


@calls.get("/api/projects/{code}/buildings/{building_id}/navigation")
def navigation(code: str, building_id: str, request: Request, may: May, studio: TheStudio, query: Query):
    may.building(code, building_id, "view")
    said = {k: (query.get(k) or [None])[0] for k in ("from", "to", "accessible")}
    accessible = (said["accessible"] or "").lower() in ("1", "true", "yes")
    return answer(request, studio.navigation(code, building_id, said["from"], said["to"], accessible))
