import json

from storeypath.assets import asset_dir, format_spec
from storeypath.package import FORMAT_VERSION, json_schemas


def test_committed_schemas_match_the_models():
    """spec/schema/ must be regenerated when the package models change:
    uv run storeypath schema ../spec/schema"""
    folder = asset_dir("spec") / "schema"
    committed = {p.name: json.loads(p.read_text()) for p in folder.glob("*.json")}
    assert committed == json_schemas()


def test_spec_documents_the_current_version():
    major_minor = ".".join(FORMAT_VERSION.split(".")[:2])
    assert f"version {major_minor}" in format_spec().splitlines()[0]
