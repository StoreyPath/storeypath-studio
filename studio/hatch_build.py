"""Build hook: copy the repository's spec/ and viewer/ folders into the package
(as storeypath/_bundled/) so installed wheels have them. When building from an
sdist the copies are already there."""

import shutil
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

ASSETS = ("spec", "viewer")


class BundleAssets(BuildHookInterface):
    def initialize(self, version, build_data):
        project = Path(self.root)
        target = project / "src" / "storeypath" / "_bundled"
        for name in ASSETS:
            source = project.parent / name
            if not source.is_dir():
                continue  # building from an sdist: already bundled
            dest = target / name
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(source, dest, ignore=shutil.ignore_patterns("node_modules", ".*"))
