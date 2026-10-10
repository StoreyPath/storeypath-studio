"""Every icon Studio's pages name is in their sprite (review_app/icons/lucide.svg): an
icon missing there is drawn as nothing, silently. And the sprite's licence is beside it."""

import re
from pathlib import Path

APP = Path(__file__).parent.parent / "src" / "storeypath" / "review_app"


def _named() -> dict[str, set[str]]:
    """The icons each page file names: icon("…"), icon: "…", ico: "…", lucide.svg#…."""
    found = {}
    for path in [*APP.glob("*.html"), *APP.glob("*.js"), *APP.glob("review/**/*.js")]:
        text = path.read_text(encoding="utf-8")
        names = set(re.findall(r'\bicon\("([a-z0-9-]+)"', text)) | set(re.findall(r'\bicon: "([a-z0-9-]+)"', text)) \
            | set(re.findall(r'\bico: "([a-z0-9-]+)"', text)) | set(re.findall(r'lucide\.svg#([a-z0-9-]+)', text))
        if names:
            found[path.relative_to(APP).as_posix()] = names
    return found


def test_every_icon_named_is_in_the_sprite():
    sprite = (APP / "icons" / "lucide.svg").read_text(encoding="utf-8")
    have = set(re.findall(r'<symbol id="([a-z0-9-]+)"', sprite))
    named = _named()
    assert named, "no page names an icon"
    missing = {page: sorted(names - have) for page, names in named.items() if names - have}
    assert not missing, missing


def test_studio_opens_its_own_3d_page():
    """Review's 3D window and a project's Walk in 3D open world.html, Studio's page, not the
    viewer's example page (left as the viewer's own)."""
    pages = [*APP.glob("*.html"), *APP.glob("*.js"), *APP.glob("review/**/*.js")]
    assert not [p.name for p in pages if "examples/world" in p.read_text(encoding="utf-8")]
    assert "/world.html?" in (APP / "studio.js").read_text(encoding="utf-8")
    assert "/world.html?" in (APP / "review" / "topbar.js").read_text(encoding="utf-8")


def test_the_icons_licence_is_beside_them():
    licence = (APP / "icons" / "LICENSE-lucide.txt").read_text(encoding="utf-8")
    assert "ISC License" in licence and "Lucide" in licence
