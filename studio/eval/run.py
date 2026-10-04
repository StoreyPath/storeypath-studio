"""Score a language model on reading drawing text, the way Studio uses it.

    uv run python eval/run.py --url http://127.0.0.1:8090        # a running llama-server
    uv run python eval/run.py --model path/to/model.gguf           # start one

Prints accuracy and time for room labels, sheet titles and layer names, with
every miss, and the built-in rules' accuracy on the same labels for comparison.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from storeypath.llm import LocalModel, read_labels, read_layer_names, read_titles
from storeypath.profile import load_profile

HERE = Path(__file__).parent


def rows(name: str) -> list[list[str]]:
    lines = (HERE / name).read_text(encoding="utf-8").splitlines()
    return [ln.split("\t") for ln in lines if ln.strip() and not ln.startswith("#")]


def score(title: str, cases: list[tuple[str, str, str]], seconds: float | None) -> float:
    hits = sum(1 for _, want, got in cases if want == got)
    timing = f" in {seconds:.1f}s" if seconds is not None else ""
    print(f"\n{title}: {hits}/{len(cases)} = {hits / len(cases):.0%}{timing}")
    for text, want, got in cases:
        if want != got:
            print(f"   miss  {text!r:44} want {want:14} got {got}")
    return hits / len(cases)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--only", choices=["labels", "titles", "layers"])
    args = ap.parse_args()
    model = LocalModel(url=args.url, model=args.model)

    labels = rows("labels.tsv")
    rules = load_profile("ncs")
    score("rules (ncs profile) on labels",
          [(t, want, (lambda ty: "-" if ty == "unspecified" else ty)(rules.classify(t, [], "")[0].value))
           for t, want in labels], None)

    if args.only in (None, "labels"):
        def got(r):
            return "missing" if r is None else (r.type.value if r.is_space else "-")

        start = time.monotonic()
        read = read_labels(model, [t for t, _ in labels])
        took = time.monotonic() - start
        score(f"{model.name}: room labels, room or not", [(t, want, got(read.get(t))) for t, want in labels], took)

        rooms = [(t, want) for t, want in labels if want != "-"]
        start = time.monotonic()
        forced = read_labels(model, [t for t, _ in rooms], rooms_only=True)
        took = time.monotonic() - start
        score(f"{model.name}: labels inside rooms, type only", [(t, want, got(forced.get(t))) for t, want in rooms],
              took)

        # As Studio reads them: the rules where they know the word, the model otherwise.
        combined = []
        for t, want in rooms:
            rule = rules.classify(t, [], "")[0].value
            combined.append((t, want, rule if rule != "unspecified" else got(forced.get(t))))
        score(f"rules, then {model.name}: labels inside rooms", combined, None)

    if args.only in (None, "titles"):
        titles = rows("titles.tsv")
        start = time.monotonic()
        read = read_titles(model, [t for t, *_ in titles])
        took = time.monotonic() - start
        cases = []
        for t, kind, floor, building in titles:
            r = read.get(t)
            got_kind = r.kind if r else "missing"
            got_floor = "-" if not r or r.floor is None else str(r.floor)
            cases.append((t, f"{kind}/{floor}", f"{got_kind}/{got_floor}"))
        score(f"{model.name}: sheet titles (kind/floor)", cases, took)

    if args.only in (None, "layers"):
        layers = rows("layers.tsv")
        start = time.monotonic()
        read = read_layer_names(model, [t for t, _ in layers])
        took = time.monotonic() - start
        score(f"{model.name}: layer names", [(t, want, read.get(t, "missing")) for t, want in layers], took)

    model.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
