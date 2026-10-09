// What a machine's graphics are, from the renderer its browser names: a software
// renderer (SwiftShader, llvmpipe, Microsoft Basic Render) draws the 3D world a frame
// every second or so; a virtual desktop's or integrated graphics draw its High quality
// slowly. No three.js here: viewer/world's webglSupport, asked before the world is
// loaded, reads it too.

export const SOFTWARE = /swiftshader|llvmpipe|softpipe|lavapipe|software|basic render|mesa offscreen/i;
export const VIRTUAL = /vmware|virtualbox|parallels|citrix|hyper-v|remotefx|svga3d|virgl|qxl|chromium os virtual|gdi generic/i;
// (not Apple's M-series, nor Intel Arc)
export const INTEGRATED = /\bintel\b(?!.*\barc\b)|\b(?:uhd|iris)\b|\bmali\b|adreno|powervr|apple a\d|videocore|vivante|tegra|radeon(?:\(tm\))? (?:vega \d+ )?graphics/i;

/** The quality a renderer (as the browser names it) gets on its own: "low" for a
 * software, virtual or integrated one (and ``why``), else "high". */
export function qualityFor(name) {
  const s = String(name ?? "");
  const why = SOFTWARE.test(s) ? "software" : VIRTUAL.test(s) ? "virtual" : INTEGRATED.test(s) ? "integrated" : null;
  return { quality: why ? "low" : "high", why };
}

/** The renderer a WebGL context draws with, as the browser names it ("" when it does not say). */
export function rendererName(gl) {
  try {
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? "");
  } catch {
    return "";
  }
}
