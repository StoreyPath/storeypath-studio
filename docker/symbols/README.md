# Symbol spotting (optional, research use only)

`docker/fetch-symbols.sh` puts SymPoint-V2 here: its code in `SymPointV2/` and its
trained weights in `weights/`. Everything in this folder but this file is
git-ignored and never part of StoreyPath.

When it is here, `docker build` installs PyTorch (CPU only) and bakes the model into
the image at `/opt/storeypath/symbols`; Studio then types the rooms that carry no name
by the fixtures drawn in them (a toilet and a bath: a bathroom; a flight of stairs
filling the room: stairs) and flags each one for review. When it is not here, the image
is built without it and works as before.

SymPoint-V2's repository states no licence and its weights were trained on
FloorPlanCAD (CC BY-NC 4.0), so it is for research only: do not publish or sell
images built with it. Release images are built without it unless the repository
variable `STOREYPATH_SYMBOLS` is set to `research`.

Outside Docker: `uv sync --extra symbols` in `studio/`, then point
`STOREYPATH_SYMBOLS` at this folder.
