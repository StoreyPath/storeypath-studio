"""Runs SymPoint-V2 in a process of its own: ``python -m storeypath.symbols.worker
<symbols folder>``, a plan's primitives as JSON on stdin, the symbols found as JSON
on stdout.

SymPoint-V2 (github.com/nicehuster/SymPointV2) is fetched by docker/fetch-symbols.sh,
not part of StoreyPath: its repository states no licence, so it is used for research
only. It was written for NVIDIA GPUs; here it runs on the CPU, with its CUDA point
operations replaced (pointops.py) and its CUDA tensor calls made CPU ones. That is done
in this process only, so Studio itself never imports PyTorch.
"""

import json
import sys
import types
from pathlib import Path

import numpy as np


def _prepare(folder: Path):
    import torch

    # stubs for what SymPoint-V2 imports only to train
    stubs = {
        "detectron2": {}, "detectron2.utils": {}, "detectron2.solver": {},
        "detectron2.utils.comm": {"get_world_size": lambda: 1},
        "detectron2.solver.build": {"maybe_add_gradient_clipping": lambda *a, **k: None},
        "tensorboardX": {"SummaryWriter": type("SummaryWriter", (), {
            "__init__": lambda self, *a, **k: None, "__getattr__": lambda self, n: (lambda *a, **k: None)})},
    }
    for name, attrs in stubs.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
    # its CUDA point operations: ours, on the CPU
    from storeypath.symbols import pointops

    package = types.ModuleType("modules.pointops.functions")
    package.pointops = pointops
    sys.modules["modules.pointops"] = types.ModuleType("modules.pointops")
    sys.modules["modules.pointops.functions"] = package
    sys.modules["modules.pointops.functions.pointops"] = pointops
    # its CUDA tensors and .cuda() calls: CPU ones
    for kind in ("Int", "Float", "Long", "Bool"):
        setattr(torch.cuda, f"{kind}Tensor", getattr(torch, f"{kind}Tensor"))
    torch.Tensor.cuda = lambda self, *a, **k: self
    sys.path.insert(0, str(folder / "SymPointV2"))


def _features(data: dict):
    """The network's input, as SymPoint-V2's loader (svgnet/data/svg3.py) makes it:
    per primitive its middle, and angle, length, type and line weight."""
    args = np.array(data["args"], dtype=float).reshape(-1, 8)
    width, height = data["width"], data["height"]
    x1, y1, x2, y2 = args[:, 0], args[:, 1], args[:, 6], args[:, 7]
    with np.errstate(divide="ignore", invalid="ignore"):
        slopes = (y2 - y1) / (x2 - x1 + 1e-8)
    slopes[x2 - x1 == 0] = np.inf
    angles = np.arctan(slopes)
    args[:, 0::2] /= width
    args[:, 1::2] /= height
    n = args.shape[0]
    m = max(n, 2048)  # padded as the loader does
    coord = np.zeros((m, 3))
    coord[:n, 0], coord[:n, 1] = args[:, 0::2].mean(1), args[:, 1::2].mean(1)
    lengths = np.zeros(m)
    lengths[:n] = data["lengths"]
    feat = np.zeros((m, 7))
    feat[:n, 0] = angles
    feat[:n, 1] = np.clip(data["lengths"], 0, max(width, height)) / max(width, height)
    feat[:n, 2:6] = np.eye(4)[data["commands"]]
    widths = np.array(data["widths"], dtype=float)
    feat[:n, 6] = widths / max(widths.max(), 1e-9)
    layers = np.array(data["layers"])
    layer_ids = np.full(m, layers.max() + 1)
    layer_ids[:n] = layers
    coord -= coord.mean(0)  # "mean" normalisation, over the padded rows too
    return coord, feat, lengths, layer_ids, n


def main() -> None:
    folder = Path(sys.argv[1])
    _prepare(folder)
    import torch
    import yaml
    from munch import Munch
    from svgnet.data.svg import SVG_CATEGORIES
    from svgnet.model.svgnet import SVGNet

    data = json.load(sys.stdin)
    cfg = Munch.fromDict(yaml.safe_load((folder / "weights" / "svg_pointT.yaml").read_text()))
    model = SVGNet(cfg.model)
    state = torch.load(folder / "weights" / "best.pth", map_location="cpu", weights_only=False)["net"]
    model.load_state_dict({k.replace("module.", "", 1): v for k, v in state.items()}, strict=False)
    model.eval()
    coord, feat, lengths, layer_ids, n = _features(data)
    m = coord.shape[0]
    label = np.zeros((m, 2), dtype=np.int64)
    label[:, 0], label[:, 1] = 35, -1  # no ground truth
    batch = (torch.tensor(coord, dtype=torch.float32), torch.tensor(feat, dtype=torch.float32), torch.tensor(label),
             torch.IntTensor([m]), torch.tensor(lengths, dtype=torch.float32), torch.tensor(layer_ids, dtype=torch.long))
    with torch.no_grad():
        result = model(batch, return_loss=False)
    names = [c["name"] for c in SVG_CATEGORIES]
    out = {"classes": [names[i] for i in result["semantic_scores"][:n].argmax(1).tolist()],
           "symbols": [{"label": names[i["labels"]], "score": round(float(i["scores"]), 3),
                        "members": np.where(i["masks"][:n])[0].tolist()} for i in result["instances"]]}
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
