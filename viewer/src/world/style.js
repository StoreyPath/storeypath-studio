// How the 3D world is drawn: its looks and its qualities, and which quality a machine
// gets when it is left to choose ("auto").
//
// Looks ("style"):
//   real:  real but clean — floors finished by room type (finishes.js), plaster walls,
//          soft sunlight and shadows, ambient occlusion where surfaces meet
//   model: an architectural model — white clay, floors lightly tinted by room type,
//          dark lines along edges, stronger soft occlusion
// Qualities (and which a machine gets on its own: gpu.js):
//   high:  every effect: occlusion and multisampled edges (post.js), a finer shadow
//          map, finer finishes, the screen's pixels up to 1.5 a CSS pixel
//   low:   no post-processing, a coarser shadow map, plain finishes, a CSS pixel a pixel:
//          for integrated graphics, virtual desktops and software renderers
// Geometry is the same in every look and quality: switching changes materials, lights
// and passes, and builds nothing again.

import * as THREE from "three";

export const STYLES = {
  real: {
    toneMapping: THREE.NeutralToneMapping, exposure: 0.92,
    environment: 0.45, hemisphere: [0xeef3fa, 0x9b9488, 0.45], sun: [0xfff0dc, 2.9],
    shadow: { radius: 5, bias: -0.0002, normalBias: 0.02 },
    // walking, indoors: the ceiling keeps the sun out but at the windows, and the
    // nearest ceiling panels light the room (spot lights straight down, no shadows)
    walk: { hemisphere: 0.32, environment: 0.3, sun: 2.6, panels: 14 },
    sky: ["#9fc0e3", "#e6eaed"], fog: 0xe6eaed,
    // occlusion over metres close up; seen from afar, a share of the distance looked over
    ao: { radius: 0.45, overview: 0.04, intensity: 3, samples: 16, denoise: 16, denoiseRadius: 12 },
    edges: false,
  },
  model: {
    toneMapping: THREE.NeutralToneMapping, exposure: 0.95,
    environment: 0.5, hemisphere: [0xffffff, 0xcfccc6, 0.8], sun: [0xffffff, 2.2],
    shadow: { radius: 8, bias: -0.0002, normalBias: 0.02 },
    walk: { hemisphere: 0.55, environment: 0.4, sun: 2.0, panels: 11 },
    sky: ["#dfe4ea", "#f6f6f4"], fog: 0xf6f6f4,
    ao: { radius: 0.6, overview: 0.05, intensity: 3.5, samples: 16, denoise: 16, denoiseRadius: 12 },
    edges: true,
  },
};

// Low: fewer panel lights walking, made up for by more ambient light (``ambient``)
export const QUALITY = {
  high: { post: true, samples: 4, pixelRatio: 1.5, pixels: 3.6e6, shadowMap: 4096, panels: 6, ambient: 1, finish: 1024, fine: true },
  low: { post: false, samples: 0, pixelRatio: 1, pixels: Infinity, shadowMap: 2048, panels: 4, ambient: 1.5, finish: 512, fine: false },
};

/** Frames drawn slower than this (median, ms) while High settles: Low instead ("auto"). */
export const SLOW_FRAME = 40;
