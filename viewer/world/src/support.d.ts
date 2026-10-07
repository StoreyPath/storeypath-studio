/** What `webglSupport` found. */
export interface WebGLSupport {
	/** WebGL 2 on a graphics card: the 3D world will run well. */
	ok: boolean;
	/** Why not: no WebGL 2 (or only with a major performance caveat), a software renderer, or the check failed. */
	reason?: 'no-webgl' | 'software' | 'error';
	/** The renderer the browser names, when it says. */
	renderer?: string;
}

/** Whether this browser can show the 3D world well; cheap, and loads nothing. */
export declare function webglSupport(): WebGLSupport;
