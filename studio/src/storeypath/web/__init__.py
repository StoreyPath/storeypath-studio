"""StoreyPath Studio on the web: its pages and JSON API as a FastAPI app.

    create_app(studio, accounts=…, …)   the app (app.py): every call asks server.Gate first

guard.py checks each request as it comes (Studio's names, where a change comes from,
sizes, who is asking behind a proxy); calls.py is what every call shares; account.py,
projects.py, floors.py, events.py and pages.py are the calls themselves.
"""

from .app import create_app

__all__ = ["create_app"]
