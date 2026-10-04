"""Command line: ``storeypath <command>``."""

from __future__ import annotations

import functools
import http.server
import json
import os
import threading
import webbrowser
from pathlib import Path
from typing import Annotated, Optional

import typer

from .assets import asset_dir
from .cad import DrawingError
from .convert import convert_floor
from .export import ExportError, export_package
from .package import json_schemas
from .profile import builtin_profiles, load_profile
from .types import SpaceType
from .validate import validate_package
from .workspace import Override, Placement, SourceDrawing, Workspace

app = typer.Typer(no_args_is_help=True, help="Convert DWG/DXF floor plans into indoor map packages.")

WorkspaceArg = Annotated[Path, typer.Argument(help="Workspace file (*.spproj)")]


def _load(path: Path) -> Workspace:
    if not path.exists():
        raise typer.BadParameter(f"{path} does not exist (create it with `storeypath new`)")
    return Workspace.load(path)


def _fail(message: str) -> None:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(1)


def _relative_to(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(path.resolve(), base.resolve())
    except ValueError:  # different drive on Windows
        return str(path.resolve())


@app.command()
def new(workspace: WorkspaceArg, name: Annotated[str, typer.Option(help="Project name")]):
    """Create a workspace for a new project (generates the project code)."""
    if workspace.exists():
        _fail(f"{workspace} already exists")
    ws = Workspace.new(name)
    ws.save(workspace)
    typer.echo(f"created project {ws.id} ({name}) in {workspace}")


@app.command("save-as-new")
def save_as_new(workspace: WorkspaceArg, target: Path, name: Annotated[str, typer.Option()]):
    """Copy a workspace as a different project with its own new project code."""
    if target.exists():
        _fail(f"{target} already exists")
    copy = _load(workspace).save_as_new_project(target, name)
    typer.echo(f"created project {copy.id} ({name}) in {target}")


@app.command("add-location")
def add_location(
    workspace: WorkspaceArg,
    code: str,
    name: Annotated[str, typer.Option()],
    address: Annotated[Optional[str], typer.Option()] = None,
):
    """Add a location (site or campus) to the project."""
    ws = _load(workspace)
    try:
        loc_id = ws.add_location(code, name, address)
    except ValueError as e:
        _fail(str(e))
    ws.save(workspace)
    typer.echo(loc_id)


@app.command("add-building")
def add_building(workspace: WorkspaceArg, location_id: str, code: str, name: Annotated[str, typer.Option()]):
    """Add a building to a location."""
    ws = _load(workspace)
    try:
        b_id = ws.add_building(location_id, code, name)
    except (KeyError, ValueError) as e:
        _fail(str(e))
    ws.save(workspace)
    typer.echo(b_id)


@app.command()
def place(
    workspace: WorkspaceArg,
    building_id: str,
    lat: Annotated[float, typer.Option(help="latitude of the anchor point")],
    lon: Annotated[float, typer.Option(help="longitude of the anchor point")],
    x: Annotated[float, typer.Option(help="drawing X of the anchor point, in --units")] = 0.0,
    y: Annotated[float, typer.Option(help="drawing Y of the anchor point, in --units")] = 0.0,
    units: Annotated[str, typer.Option(help="units of --x/--y: mm, cm, m, in, ft")] = "m",
    bearing: Annotated[float, typer.Option(help="compass bearing of the drawing's +Y axis, degrees")] = 0.0,
):
    """Put a building on the map: a drawing point, where it is on earth, and which way is north."""
    from .cad import UNIT_NAMES
    from ezdxf import units as ezunits

    if units not in UNIT_NAMES:
        _fail(f"unknown units {units}")
    f = ezunits.conversion_factor(UNIT_NAMES[units], ezunits.InsertUnits.Meters)
    ws = _load(workspace)
    try:
        ws.building(building_id).placement = Placement(lon=lon, lat=lat, x=x * f, y=y * f, bearing=bearing)
    except KeyError as e:
        _fail(str(e))
    ws.save(workspace)
    typer.echo(f"placed {building_id}")


@app.command("add-floor")
def add_floor(
    workspace: WorkspaceArg,
    building_id: str,
    drawing: Annotated[Path, typer.Argument(help="DWG or DXF file of this floor")],
    ordinal: Annotated[int, typer.Option(help="0 = ground floor, 1 = first floor, -1 = basement")],
    code: Annotated[Optional[str], typer.Option(help="floor code (default F00, F01…, B01…)")] = None,
    name: Annotated[Optional[str], typer.Option()] = None,
    elevation: Annotated[Optional[float], typer.Option(help="meters (default ordinal × height)")] = None,
    height: Annotated[float, typer.Option(help="floor-to-floor height, meters")] = 3.5,
    profile: Annotated[str, typer.Option(help="layer-mapping profile or YAML path")] = "ncs",
    units: Annotated[Optional[str], typer.Option(help="override drawing units: mm, cm, m, in, ft")] = None,
):
    """Add a floor and its source drawing to a building."""
    ws = _load(workspace)
    if not drawing.exists():
        _fail(f"{drawing} does not exist")
    load_profile(profile)  # fail early on a bad profile
    source = SourceDrawing(path=_relative_to(drawing, workspace.parent), profile=profile, units=units)
    try:
        f_id = ws.add_floor(building_id, ordinal, code=code, name=name, elevation=elevation,
                            height=height, source=source)
    except (KeyError, ValueError) as e:
        _fail(str(e))
    ws.save(workspace)
    typer.echo(f_id)


@app.command()
def convert(
    workspace: WorkspaceArg,
    floor: Annotated[Optional[str], typer.Option(help="only this floor ID")] = None,
):
    """Read the drawings and update the project's objects, keeping existing IDs."""
    ws = _load(workspace)
    floors = [fid for *_, fid in ws.iter_floors() if (floor is None or fid == floor)]
    if not floors:
        _fail("no floors to convert" if floor is None else f"no floor {floor}")
    failed = False
    for fid in floors:
        if ws.floor(fid).source is None:
            continue
        try:
            report = convert_floor(ws, fid, workspace.parent)
        except DrawingError as e:
            typer.secho(f"{fid}: {e}", fg="red", err=True)
            failed = True
            continue
        typer.echo(report.summary())
        for w in report.warnings:
            typer.secho(f"  warning: {w}", fg="yellow")
    ws.save(workspace)
    if failed:
        raise typer.Exit(1)


@app.command("list")
def list_objects(
    workspace: WorkspaceArg,
    floor: Annotated[Optional[str], typer.Option(help="only this floor ID")] = None,
    review: Annotated[bool, typer.Option(help="only spaces without a type, or without a name and number")] = False,
    retired: Annotated[bool, typer.Option(help="include retired IDs")] = False,
):
    """List objects with their IDs, types and labels."""
    ws = _load(workspace)
    for *_, fid in ws.iter_floors():
        if floor and fid != floor:
            continue
        for r in sorted(ws.floor_objects(fid, include_retired=retired), key=lambda r: r.id):
            eff = ws.effective(r)
            if review and not (
                eff["type"] == "unspecified" or (r.kind == "space" and not eff["name"] and not eff["number"])
            ):
                continue
            label = " ".join(x for x in (eff["name"], eff["number"]) if x) or "-"
            flag = " (corrected)" if eff["corrected"] else ""
            flag += " (retired)" if r.status == "retired" else ""
            typer.echo(f"{r.id}  {eff['type']:<13} {label}{flag}")


@app.command()
def fix(
    workspace: WorkspaceArg,
    object_id: str,
    type: Annotated[Optional[SpaceType], typer.Option(help="correct type")] = None,
    name: Annotated[Optional[str], typer.Option()] = None,
    number: Annotated[Optional[str], typer.Option()] = None,
    clear: Annotated[bool, typer.Option(help="remove all corrections for this object")] = False,
):
    """Correct an object's type, name or number. Corrections survive re-conversion."""
    ws = _load(workspace)
    record = ws.objects.get(object_id)
    if record is None or record.status != "active":
        _fail(f"no active object {object_id}")
    if clear:
        ws.overrides.pop(object_id, None)
    else:
        if type is not None and record.kind != "space":
            _fail("only spaces have a type to correct")
        o = ws.overrides.get(object_id, Override())
        ws.overrides[object_id] = o.model_copy(update={
            k: v for k, v in (("type", type), ("name", name), ("number", number)) if v is not None
        })
    ws.save(workspace)
    eff = ws.effective(record)
    typer.echo(f"{object_id}  {eff['type']}  {eff['name'] or ''} {eff['number'] or ''}".rstrip())


@app.command()
def export(
    workspace: WorkspaceArg,
    output: Annotated[Path, typer.Option("-o", "--output", help="package file (*.storeypath)")],
):
    """Write the exchange package."""
    ws = _load(workspace)
    try:
        manifest = export_package(ws, output)
    except ExportError as e:
        _fail(str(e))
    ws.save(workspace)
    errors = validate_package(output)
    counts = ", ".join(f"{n} {k}" for k, n in manifest.counts.items())
    typer.echo(f"export #{manifest.export.sequence} → {output} ({counts})")
    if errors:
        _fail("the package failed validation:\n  " + "\n  ".join(errors))


@app.command()
def validate(package: Path):
    """Check a package against the format."""
    errors = validate_package(package)
    if errors:
        _fail("\n".join(errors))
    typer.echo(f"{package}: valid")


@app.command()
def schema(output_dir: Path):
    """Write the JSON Schemas of the package files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, s in json_schemas().items():
        (output_dir / name).write_text(json.dumps(s, indent=2))
    typer.echo(f"wrote {len(json_schemas())} schemas to {output_dir}")


@app.command()
def profiles():
    """List the built-in layer-mapping profiles."""
    for name in builtin_profiles():
        typer.echo(f"{name}: {load_profile(name).description}")


@app.command()
def view(
    package: Path,
    port: Annotated[int, typer.Option()] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
):
    """Open a package in the StoreyPath Viewer example app."""
    if not package.exists():
        _fail(f"{package} does not exist")
    viewer_dir = asset_dir("viewer")

    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            if path.split("?")[0] == "/package.storeypath":
                return str(package.resolve())
            return super().translate_path(path)

        def log_message(self, *args):
            pass

    handler = functools.partial(Handler, directory=str(viewer_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/examples/basic/?pkg=/package.storeypath"
    typer.echo(f"viewer at {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


@app.command()
def demo(directory: Path):
    """Generate sample drawings, a workspace and a package to try things out."""
    from .samples import build_demo

    if directory.exists() and any(directory.iterdir()):
        _fail(f"{directory} is not empty")
    ws_path, pkg_path = build_demo(directory)
    typer.echo(f"workspace: {ws_path}\npackage:   {pkg_path}\nnext: storeypath view {pkg_path}")


def main() -> None:
    app()
