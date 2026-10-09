"""Area samples: a small file a person downloads from Review for an area of a floor
(at most 50 × 50 m) and sends to the StoreyPath team, to show how Studio read it:

- ``drawing.dxf``: the drawing's part in the area (cut.py), moved to (0, 0);
- ``drawing.png`` and ``reading.png``: the area as drawn, and with Studio's reading
  drawn over it (picture.py);
- ``reading.json``: what Studio decided there and why; ``corrections.json``: what
  people changed there (content.py);
- ``manifest.json`` and ``README.txt``.

Private information is always taken out (private.py): people's names, phone numbers,
emails and the like, the project's, site's, building's and floor's names and codes,
Studio's IDs, where the area is. The file is a ZIP named ``<id>.spsample``; the format,
the privacy rules and how to analyse one are in docs/AREA-SAMPLES.md. ``storeypath
sample inspect`` and ``storeypath sample replay`` read one (tools.py).
"""

from .frame import MAX_SIDE_M, Area
from .make import SAMPLE_FORMAT, SAMPLE_VERSION, SUFFIX, Sample, build, make, preview

__all__ = ["MAX_SIDE_M", "Area", "SAMPLE_FORMAT", "SAMPLE_VERSION", "SUFFIX", "Sample", "build", "make", "preview"]
