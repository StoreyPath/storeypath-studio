// Whether this browser can show the 3D world well, asked before the world (and
// three.js with it) is loaded: WebGL 2 on a real graphics card. Software
// renderers (SwiftShader, llvmpipe, Microsoft Basic Render) do draw it, a frame
// every second or so, so they are refused too: a system shows the floor in 2D
// there (the SVG viewer, made for machines with no GPU).

import { SOFTWARE } from "../../src/world/gpu.js";

/**
 * @returns {{ ok: boolean, reason?: "no-webgl" | "software" | "error", renderer?: string }}
 */
export function webglSupport() {
  try {
    if (typeof document === "undefined") return { ok: false, reason: "no-webgl" };
    const canvas = document.createElement("canvas");
    // A context the browser would only give slowly (no GPU, blocklisted driver) is
    // refused here rather than given.
    const gl = canvas.getContext("webgl2", { failIfMajorPerformanceCaveat: true, powerPreference: "high-performance" });
    if (!gl) return { ok: false, reason: "no-webgl" };
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? "");
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    if (SOFTWARE.test(renderer)) return { ok: false, reason: "software", renderer };
    return { ok: true, renderer };
  } catch {
    return { ok: false, reason: "error" };
  }
}
