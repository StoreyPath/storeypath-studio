# Bundled libraries

Copies of the viewer's dependencies, so the examples (and StoreyPath Studio,
which serves them) work with no internet connection:

| File | Library | Licence |
|---|---|---|
| `maplibre-gl.js`, `maplibre-gl.css` | [MapLibre GL JS](https://maplibre.org) 4.7.1 | BSD-3-Clause, `LICENSE-maplibre-gl.txt` |
| `jszip.min.js` | [JSZip](https://stuk.github.io/jszip/) 3.10.1 | MIT (dual MIT/GPL-3.0; used under MIT), `LICENSE-jszip.markdown` |
| `three/` | [three.js](https://threejs.org) r186 (0.186.1): the core and the addons the 3D world uses | MIT, `three/LICENSE` |

`*.mjs` wrap them as ES modules for the import map. Applications that bundle the
viewer install `maplibre-gl`, `jszip` and `three` from npm instead.
