// The High quality's post-processing: the scene drawn into a multisampled buffer (its
// edges anti-aliased there: MSAA), ambient occlusion worked out from its depth at half
// resolution and brought up depth-aware, so that it has no halos at edges (N8AO,
// vendored), multiplied in; then tone mapping and sRGB (OutputPass). Loaded only for
// High (world.js imports it when it is first wanted).

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { N8AOPass } from "../../vendor/n8ao/N8AO.js";

/** The passes for a renderer, scene and camera: ``composer`` (render it instead of the
 * renderer), ``setAo(ao, radius)`` to set the occlusion (a look's ``ao``, and its reach in
 * metres), and ``dispose``. ``samples``: the multisampling. */
export function makeComposer(renderer, scene, camera, { samples = 4 } = {}) {
  const size = renderer.getDrawingBufferSize(new THREE.Vector2());
  const ao = new N8AOPass(scene, camera, size.x, size.y);
  // its scene drawn multisampled, with the depth the occlusion is worked out from
  const beauty = new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType, samples });
  beauty.depthTexture = new THREE.DepthTexture(size.x, size.y, THREE.UnsignedIntType);
  beauty.depthTexture.format = THREE.DepthFormat;
  ao.beautyRenderTarget.dispose();
  ao.beautyRenderTarget = beauty;
  ao.autoDetectTransparency = false; // nothing see-through shades what is behind it; no looking for it every frame
  Object.assign(ao.configuration, { transparencyAware: false, halfRes: true, depthAwareUpsampling: true,
    gammaCorrection: false, color: new THREE.Color(0, 0, 0) });
  const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(size.x, size.y, { type: THREE.HalfFloatType }));
  composer.addPass(ao);
  composer.addPass(new OutputPass());
  let was = null;
  return {
    composer,
    setAo(look, radius) {
      const want = { aoSamples: look.samples, denoiseSamples: look.denoise, denoiseRadius: look.denoiseRadius,
        intensity: look.intensity, aoRadius: Math.round(radius * 100) / 100, distanceFalloff: 1 };
      const key = JSON.stringify(want);
      if (key === was) return;
      was = key;
      for (const [k, v] of Object.entries(want)) if (ao.configuration[k] !== v) ao.configuration[k] = v;
    },
    dispose() {
      // N8AO's pass has no dispose of its own: its targets, quads, materials and noise
      for (const v of Object.values(ao)) {
        if (v && typeof v.dispose === "function" && (v.isWebGLRenderTarget || v.isTexture || v.isMaterial || v._mesh)) v.dispose();
      }
      for (const pass of composer.passes) pass.dispose?.();
      composer.dispose();
    },
  };
}
