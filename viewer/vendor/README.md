# Bundled libraries

Copies of the viewer's dependencies, so the examples (and StoreyPath Studio,
which serves them) work with no internet connection:

| File | Library | Licence |
|---|---|---|
| `maplibre-gl.js`, `maplibre-gl.css` | [MapLibre GL JS](https://maplibre.org) 4.7.1 | BSD-3-Clause, `LICENSE-maplibre-gl.txt` |
| `jszip.min.js` | [JSZip](https://stuk.github.io/jszip/) 3.10.1 | MIT (dual MIT/GPL-3.0; used under MIT), `LICENSE-jszip.markdown` |
| `three/` | [three.js](https://threejs.org) r186 (0.186.1): the core and the addons the 3D world uses (its glTF loader and exporter for the floors pre-built by `../world/bake.mjs`; `postprocessing/` and `shaders/`: the EffectComposer and OutputPass of its High quality) | MIT, `three/LICENSE` |
| `n8ao/N8AO.js` | [N8AO](https://github.com/N8python/n8ao) 2.0.1: the 3D world's ambient occlusion (High quality). Two imports changed: three's `Pass` from the import map's `three/addons/`, and a stand-in for pmndrs `postprocessing`'s (only its `N8AOPostPass` needs that library, and it is not used) | CC0-1.0 (public domain), `n8ao/LICENSE` |

`*.mjs` wrap them as ES modules for the import map. Applications that bundle the
viewer install `maplibre-gl`, `jszip` and `three` from npm instead.
