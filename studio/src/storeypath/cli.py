"""Command line: ``storeypath <command>``."""

from __future__ import annotations

import functools
import http.server
import json
import os
import signal
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
from .profile import AUTO, builtin_profiles, load_profile, resolve_profile
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
    height: Annotated[float, typer.Option(help="floor-to-floor height, meters (see `storeypath levels`)")] = 3.5,
    parapet: Annotated[Optional[float], typer.Option(
        help="height of the low walls around its terraces and balconies, meters (default 1.1)")] = None,
    profile: Annotated[str, typer.Option(help="auto (read from the drawing), a built-in profile or a YAML path")] = "auto",
    units: Annotated[Optional[str], typer.Option(help="override drawing units: mm, cm, m, in, ft")] = None,
    view: Annotated[Optional[str], typer.Option(
        help="the plan to use when the drawing holds several: its number or part of its title (see `storeypath views`)")] = None,
    region: Annotated[Optional[str], typer.Option(
        help="the part of the drawing with this floor's plan: x0,y0,x1,y1 in drawing units")] = None,
    use_model: Annotated[bool, typer.Option("--model/--no-model",
                                            help="read notes that state the units with the local language model")] = True,
):
    """Add a floor and its source drawing to a building. The units the drawing is
    read in are kept with the floor: --units, or the units the drawing shows."""
    ws = _load(workspace)
    if not drawing.exists():
        _fail(f"{drawing} does not exist")
    prof = load_profile(profile)  # fail early on a bad profile
    profile_ref = _relative_to(Path(profile), workspace.parent) if Path(profile).suffix in (".yaml", ".yml") else profile
    if units is None:
        decision = _units(drawing, use_model)
        units = decision.units
        if not decision.sure:
            typer.secho(f"{decision.reason} Re-add the floor with --units to choose.", fg="yellow", err=True)
    source = SourceDrawing(path=_relative_to(drawing, workspace.parent), profile=profile_ref, units=units)
    if region:
        try:
            x0, y0, x1, y1 = (float(v) for v in region.split(","))
        except ValueError:
            _fail("--region needs four numbers: x0,y0,x1,y1")
        source.region = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    elif view:
        source.region, source.view = _pick_view(drawing, prof, units, view, profile)
    try:
        f_id = ws.add_floor(building_id, ordinal, code=code, name=name, elevation=elevation,
                            height=height, parapet_height=parapet, source=source)
    except (KeyError, ValueError) as e:
        _fail(str(e))
    ws.save(workspace)
    typer.echo(f_id)


def _units(drawing: Path, use_model: bool):
    """The units a drawing shows (see reading.read_units)."""
    from .cad import read_drawing
    from .llm import LocalModel
    from .reading import read_units

    try:
        doc = read_drawing(drawing)
    except DrawingError as e:
        _fail(str(e))
    model = LocalModel() if use_model else None
    try:
        return read_units(doc, model)
    finally:
        if model is not None:
            model.close()


def _rule_reader(prof):
    """Room-name test for finding plans: the rules' words, levels and tags."""
    from .reading import NOT_A_ROOM
    from .types import SpaceType

    def is_room_name(text: str):
        if NOT_A_ROOM.match(text):
            return False
        return True if prof.classify(text, [], "")[0] != SpaceType.UNSPECIFIED else None
    return is_room_name


def _pick_view(drawing: Path, prof, units, query: str, prof_name: str = "auto"):
    from .cad import meters_per_unit, read_drawing
    from .sheets import find_views

    try:
        doc = read_drawing(drawing)
    except DrawingError as e:
        _fail(str(e))
    found = [v for v in find_views(doc, prof, meters_per_unit(doc, units), auto=prof_name == AUTO,
                                    is_room_name=_rule_reader(prof)) if v.matches(query)]
    if len(found) != 1:
        names = "; ".join(f"#{v.index} {v.title or '(untitled)'}" for v in found) or "none"
        _fail(f"--view {query!r} must match exactly one plan (matches: {names}); see `storeypath views {drawing}`")
    return found[0].region, found[0].title


@app.command()
def views(
    drawing: Annotated[Path, typer.Argument(help="DWG or DXF file")],
    profile: Annotated[str, typer.Option(help="auto, a built-in layer-mapping profile or a YAML path")] = "auto",
    units: Annotated[Optional[str], typer.Option(help="override drawing units: mm, cm, m, in, ft")] = None,
    all_views: Annotated[bool, typer.Option("--all", help="also list elevations, sections and details")] = False,
    use_model: Annotated[bool, typer.Option("--model/--no-model",
                                            help="read notes that state the units with the local language model")] = True,
):
    """List the plans in a drawing that holds several side by side, and the units
    it is read in."""
    from .cad import meters_per_unit, read_drawing
    from .sheets import find_views

    if units is None:
        decision = _units(drawing, use_model)
        units = decision.units
        typer.echo(decision.reason + ("" if decision.sure else " Choose with --units.") + "\n")
    try:
        doc = read_drawing(drawing)
    except DrawingError as e:
        _fail(str(e))
    prof = load_profile(profile)
    found = [v for v in find_views(doc, prof, meters_per_unit(doc, units), auto=profile == AUTO,
                                   is_room_name=_rule_reader(prof)) if all_views or v.is_plan]
    if not found:
        _fail("no plans found: check the profile's wall layers and the units")
    typer.echo(f"{'#':>3}  {'title':<40} {'size (m)':>13}  floor  region (drawing units)")
    for v in found:
        floor = "" if v.ordinal is None else str(v.ordinal)
        region = ",".join(f"{round(r, 2):g}" for r in v.region)
        typer.echo(f"{v.index:>3}  {(v.title or '(untitled)')[:40]:<40} {v.size[0]:>6} x {v.size[1]:<6} {floor:>5}  {region}")
    typer.echo("\nuse one with: storeypath add-floor <workspace> <building> <drawing> --view <# or title>")


@app.command()
def private(
    drawing: Annotated[Path, typer.Argument(help="DWG or DXF file")],
    output: Annotated[Path, typer.Argument(help="the copy to write (.dxf)")],
    use_model: Annotated[bool, typer.Option("--model/--no-model",
                                            help="read the texts left with the local language model too")] = True,
):
    """Copy a drawing without its private information: each sheet's title block
    (client, owner, consultant, who drew it, stamps, logos), names, phone numbers
    and emails elsewhere, images, paper-space sheets and the file's hidden data.
    Studio does this to every drawing it is given unless told not to."""
    from .cad import read_drawing_to_change
    from .privacy import make_private

    if output.suffix.lower() != ".dxf":
        _fail("the copy is written as DXF: name it *.dxf")
    try:
        doc = read_drawing_to_change(drawing)
    except DrawingError as e:
        _fail(str(e))
    from .llm import LocalModel

    model = LocalModel() if use_model else None
    try:
        report = make_private(doc, model, lambda line: typer.echo(line, err=True))
    finally:
        if model is not None:
            model.close()
    doc.saveas(output)
    typer.echo(f"{report.summary()}; written to {output}")


@app.command()
def words(drawing: Annotated[Path, typer.Argument(help="DWG or DXF file")]):
    """Every word and string in a drawing (written on it, attributes, layer and block
    names, sheet setups, file settings), to look through for anything private."""
    from .cad import read_drawing
    from .privacy import words as listed

    try:
        typer.echo(listed(read_drawing(drawing), drawing.name), nl=False)
    except DrawingError as e:
        _fail(str(e))


@app.command()
def align(
    workspace: WorkspaceArg,
    building_id: str,
    reference: Annotated[Optional[str], typer.Option(help="floor ID the others are lined up with (default: lowest)")] = None,
):
    """Line up a building's floors drawn side by side in one drawing (or shifted
    between drawings): finds where each plan's walls overlap the reference floor's."""
    from .cad import meters_per_unit, read_drawing
    from .sheets import align as align_walls, floor_walls

    ws = _load(workspace)
    try:
        b = ws.building(building_id)
    except (KeyError, ValueError) as e:
        _fail(str(e))
    floors = sorted((f for f in b.floors if f.source), key=lambda f: f.ordinal)
    if len(floors) < 2:
        _fail("align needs at least two floors with drawings")
    ref = next((f for f in floors if f"{building_id}-{f.code}" == reference), None) if reference else floors[0]
    if ref is None:
        _fail(f"no floor {reference} in {building_id}")
    docs: dict[str, object] = {}

    def walls(f):
        src = f.source
        if src.path not in docs:
            docs[src.path] = read_drawing(workspace.parent / src.path)
        doc = docs[src.path]
        scale = meters_per_unit(doc, src.units)
        profile = load_profile(resolve_profile(src.profile, workspace.parent))
        if src.profile == AUTO:  # each plan's wall layers, read from what is drawn
            from .analyse import analyse

            profile = analyse(doc, scale, src.region, None, profile).profile
        return floor_walls(doc, profile, scale, src.region), scale

    try:
        ref_walls, _ = walls(ref)
        ref_offset = ref.source.offset or (0.0, 0.0)
        for f in floors:
            if f is ref:
                continue
            other, scale = walls(f)
            (tx, ty), overlap = align_walls(ref_walls, other)
            f.source.offset = (round(ref_offset[0] + tx / scale, 6), round(ref_offset[1] + ty / scale, 6))
            note = "" if overlap >= 0.3 else "  (little overlap: check this floor in review)"
            typer.echo(f"{building_id}-{f.code}: shifted {tx:.3f}, {ty:.3f} m onto {building_id}-{ref.code}; "
                       f"{overlap:.0%} of its walls line up{note}")
    except DrawingError as e:
        _fail(str(e))
    ws.save(workspace)
    typer.echo(f"now run: storeypath convert {workspace}")


@app.command()
def levels(
    workspace: WorkspaceArg,
    building_id: str,
    use_model: Annotated[bool, typer.Option("--model/--no-model",
                                            help="read level labels in other languages with the local language model")] = True,
):
    """Set the floors' heights from the level labels on the building's drawings
    (+3.65 FIRST FLOOR SLAB LVL on sections and elevations, or the +0.45 FFL each
    floor's plan marks), and the roof's parapet."""
    from .cad import read_drawing
    from .levels import floor_levels, plan_level, read_level_marks
    from .llm import LocalModel

    ws = _load(workspace)
    try:
        b = ws.building(building_id)
    except (KeyError, ValueError) as e:
        _fail(str(e))
    paths = list(dict.fromkeys(f.source.path for f in b.floors if f.source))
    if not paths:
        _fail(f"{building_id} has no floors with drawings")
    model = LocalModel() if use_model else None
    marks, marked = [], {}
    try:
        for path in paths:
            doc = read_drawing(workspace.parent / path)
            marks += read_level_marks(doc, model)
            for f in b.floors:  # the level each floor's plan marks ("+0.45 FFL")
                if f.source and f.source.path == path:
                    marked[f.ordinal] = plan_level(doc, f.source.region)
    except DrawingError as e:
        _fail(str(e))
    finally:
        if model is not None:
            model.close()
    found = floor_levels(marks)
    found.add_plans(marked)
    if not found.heights and not found.levels:
        _fail("no level labels found (such as +3.65 FIRST FLOOR SLAB LVL, or +0.45 FFL on the plans); "
              "set heights with add-floor --height")
    typer.echo(f"levels: {found.summary()}")
    for f in b.floors:
        f.height = found.height(f.ordinal)
        if found.parapet is not None and f.ordinal == found.roof:
            f.parapet_height = found.parapet
    ws.restack(building_id)
    for f in sorted(b.floors, key=lambda f: f.ordinal):
        parapet = f", parapets {f.parapet_height:.2f} m" if f.parapet_height else ""
        typer.echo(f"{building_id}-{f.code}: {f.elevation:.2f} m up, {f.height:.2f} m high{parapet}")
    ws.save(workspace)


@app.command()
def convert(
    workspace: WorkspaceArg,
    floor: Annotated[Optional[str], typer.Option(help="only this floor ID")] = None,
    use_model: Annotated[bool, typer.Option("--model/--no-model",
                                            help="read unknown room names with the local language model")] = True,
    use_symbols: Annotated[bool, typer.Option("--symbols/--no-symbols",
                                              help="type unnamed rooms by the fixtures drawn in them, when "
                                                   "SymPoint-V2 is installed (research use only)")] = True,
    use_vision: Annotated[bool, typer.Option("--vision/--no-vision",
                                             help="look at every room with the vision model "
                                                  "($STOREYPATH_VISION_URL), when one is set")] = True,
):
    """Read the drawings and update the project's objects, keeping existing IDs."""
    from .llm import LocalModel
    from .symbols import SymbolSpotter

    ws = _load(workspace)
    model = LocalModel() if use_model else None
    if model is not None and model.available():
        typer.echo(f"reading texts with {model.name}")
    symbols = SymbolSpotter() if use_symbols else None
    if symbols is not None and symbols.available():
        typer.echo(f"spotting symbols with {symbols.name} (research use only)")
    from .vision import VisionModel

    vision = VisionModel() if use_vision else None
    if vision is not None and vision.available():
        typer.echo(f"looking at the rooms with {vision.name}")
    elif vision is not None and vision.url:
        typer.secho(f"  warning: {vision.failed}", fg="yellow")
    floors = [fid for *_, fid in ws.iter_floors() if (floor is None or fid == floor)]
    if not floors:
        _fail("no floors to convert" if floor is None else f"no floor {floor}")
    failed = False
    for fid in floors:
        if ws.floor(fid).source is None:
            continue
        try:
            report = convert_floor(ws, fid, workspace.parent, model, symbols,
                                   vision if vision is not None and vision.available() else None,
                                   say=lambda m: typer.echo(f"  {m}"))
        except DrawingError as e:
            typer.secho(f"{fid}: {e}", fg="red", err=True)
            failed = True
            continue
        typer.echo(report.summary())
        for w in report.warnings:
            typer.secho(f"  warning: {w}", fg="yellow")
    ws.save(workspace)
    if model is not None:
        model.close()
    if failed:
        raise typer.Exit(1)


@app.command("list")
def list_objects(
    workspace: WorkspaceArg,
    floor: Annotated[Optional[str], typer.Option(help="only this floor ID")] = None,
    review: Annotated[bool, typer.Option(help="only spaces that need a look: no type, no name or number, merged rooms")] = False,
    retired: Annotated[bool, typer.Option(help="include retired IDs")] = False,
):
    """List objects with their IDs, types and labels."""
    ws = _load(workspace)
    for *_, fid in ws.iter_floors():
        if floor and fid != floor:
            continue
        for r in sorted(ws.floor_objects(fid, include_retired=retired), key=lambda r: r.id):
            eff = ws.effective(r)
            reasons = ws.review_reasons(r)
            if review and not reasons:
                continue
            label = " ".join(x for x in (eff["name"], eff["number"]) if x) or "-"
            flag = " (corrected)" if eff["corrected"] else ""
            flag += " (retired)" if r.status == "retired" else ""
            flag += f"  [{'; '.join(reasons)}]" if review else ""
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
    """Correct an object's type, name or number. Corrections survive re-conversion.
    With no options, accepts the object as it is (takes it off the review list).
    An empty --name "" or --number "" removes a wrongly detected one."""
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
def serve(
    data: Annotated[Path, typer.Option(help="folder holding the projects")] = Path("."),
    host: Annotated[str, typer.Option(help="0.0.0.0 to serve other machines (as in the container)")] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8080,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = False,
):
    """Run StoreyPath Studio in the browser: projects, drawings, review, export."""
    _serve(data, host, port, "/" , open_browser)


@app.command()
def review(
    workspace: WorkspaceArg,
    port: Annotated[int, typer.Option()] = 8766,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
):
    """Open the review editor: each floor over its drawing, click a space to correct it."""
    ws = _load(workspace)
    _serve(workspace.parent, "127.0.0.1", port, f"/review.html?p={ws.id}", open_browser,
           f"reviewing {ws.project.name} ({ws.id}); corrections are saved as you make them")


def _serve(data: Path, host: str, port: int, page: str, open_browser: bool, note: str = "") -> None:
    from .server import Studio, make_server

    studio = Studio(data)
    try:
        server = make_server(studio, host, port)
    except OSError as e:
        _fail(f"cannot listen on {host}:{port}: {e.strerror} (pick another with --port)")
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{server.server_port}{page}"
    status = studio.status()
    typer.echo(f"StoreyPath Studio {status['version']} at {url} (Ctrl+C to stop)")
    typer.echo(f"projects in {studio.data.resolve()}; language model: {status['model'] or 'none'}; "
               f"DWG: {'yes' if status['dwg'] else 'no (DXF only)'}"
               + (f"; symbols: {status['symbols']} (research use only)" if status["symbols"] else "")
               + (f"; vision: {status['vision']}" if status["vision"] else ""))
    if note:
        typer.echo(note)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    # `kill` and `docker stop` send SIGTERM: stop as for Ctrl+C, so llama-server stops too.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        studio.model.close()


@app.command()
def export(
    workspace: WorkspaceArg,
    output: Annotated[Path, typer.Option("-o", "--output", help="package file (*.storeypath)")],
):
    """Write the exchange package."""
    ws = _load(workspace)
    try:
        manifest = export_package(ws, output, say=typer.echo)
    except ExportError as e:
        _fail(str(e))
    ws.save(workspace)
    errors = validate_package(output)
    counts = ", ".join(f"{n} {k}" for k, n in manifest.counts.items())
    typer.echo(f"export #{manifest.export.sequence} → {output} ({counts})")
    loose = [b for b, p in manifest.placements.items() if not p.placed]
    if loose:
        typer.echo(f"not on the map yet (exported around 0°N 0°E; `storeypath place` when known): {', '.join(loose)}")
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
    import logging

    logging.getLogger("ezdxf").setLevel(logging.ERROR)  # font and repair chatter from real drawings
    app()
