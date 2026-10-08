"""StoreyPath Studio on the web: its pages and JSON API as a FastAPI app, served by
uvicorn, over HTTPS by default.

    create_app(studio, accounts=…, …)   the app (app.py): every call asks server.Gate first
    make_server(studio, host, port, …)  the app served on a port (serve.py), as
                                        ``storeypath serve`` and ``storeypath review`` run it

guard.py checks each request as it comes (Studio's names, where a change comes from,
sizes, who is asking behind a proxy); calls.py is what every call shares; account.py,
projects.py, floors.py, events.py and pages.py are the calls themselves.
"""

from .app import create_app
from .serve import make_server

__all__ = ["create_app", "make_server"]
