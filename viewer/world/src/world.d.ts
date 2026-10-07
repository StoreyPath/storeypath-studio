// Declarations of @storeypath/viewer-world (dist/world.js): StoreyPath's 3D
// world and the package reader it opens packages with. Feature properties are
// as spec/FORMAT.md describes them.

export { webglSupport, type WebGLSupport } from './support.js';

export declare const FORMAT: 'storeypath-package';
export declare const SUPPORTED_MAJOR_VERSION: number;
/** Default colour of each space type (CSS colours). */
export declare const TYPE_COLORS: Readonly<Record<string, string>>;

export type LonLat = [number, number];

export interface Geometry {
	type: string;
	coordinates: unknown;
}

/** A GeoJSON feature of a package: a location, building, floor, space, zone or opening. */
export interface Feature<P = Record<string, unknown>> {
	type: 'Feature';
	id: string;
	geometry: Geometry | null;
	properties: P;
}

export interface Manifest {
	format: 'storeypath-package';
	format_version: string;
	project: { id: string; name: string };
	files: Record<string, string>;
	/** Only some of the project's buildings (format 0.4); absent: all of them. */
	scope?: { buildings: string[] } | null;
	[key: string]: unknown;
}

/** A package as read: its features, and ways to find them. */
export declare class StoreyPathPackage {
	constructor(
		manifest: Manifest,
		collections: { location: Feature[]; buildings: Feature[]; floors: Feature[]; spaces: Feature[]; openings: Feature[]; zones?: Feature[] }
	);
	readonly manifest: Manifest;
	readonly locations: Feature[];
	readonly buildings: Feature[];
	readonly floors: Feature[];
	/** What walls and doors enclose. */
	readonly spaces: Feature[];
	/** The parts of open spaces, with no wall between them. */
	readonly zones: Feature[];
	readonly openings: Feature[];
	/** What is used: the zones of divided spaces and the other spaces. */
	readonly units: Feature[];
	readonly project: { id: string; name: string };
	/** The feature with this ID, or null. */
	get(id: string): Feature | null;
	/** The buildings the package holds when it is part of a project, or null for the whole project. */
	readonly scope: string[] | null;
	/** Whether the package holds a building: always for the whole project; for a part, when it is in the scope. */
	holds(buildingId: string): boolean;
	/** Floors of a building, lowest first. */
	floorsOf(buildingId: string): Feature[];
	spacesOn(floorId: string): Feature[];
	zonesOf(spaceId: string): Feature[];
	unitsOn(floorId: string): Feature[];
	groundFloor(buildingId: string): Feature | null;
	hierarchy(id: string): {
		project: { id: string; name: string };
		location: Feature | null;
		building: Feature | null;
		floor: Feature | null;
		object: Feature | null;
	};
	doorsOf(spaceId: string): { door: Feature; space: Feature | null }[];
	search(query?: string, options?: { type?: string; buildingId?: string; floorId?: string; limit?: number }): Feature[];
}

/** Read a package from a URL, Blob, File or ArrayBuffer. */
export declare function loadPackage(source: string | URL | Blob | ArrayBuffer | Uint8Array): Promise<StoreyPathPackage>;

export interface WorldOptions {
	/** Floor slab thickness, m (0.22). */
	slab?: number;
	/** Door head above the floor, m (2.1). */
	doorHead?: number;
	windowSill?: number;
	windowHead?: number;
	/** When the package does not say, m (0.2). */
	wallThickness?: number;
	/** Walls are cut to this in the cutaway view, m (1.25). */
	cutHeight?: number;
	/** Metres between floors in the dollhouse view (0). */
	explode?: number;
	/** Room labels (true). */
	labels?: boolean;
	/** Spaces hidden or ignored in review (false). */
	showHidden?: boolean;
	/** Fog colour (0xeef1f2). */
	fog?: number;
}

export type WorldMode = 'dollhouse' | 'walk';

export interface WorldEvents {
	load: { package: StoreyPathPackage };
	buildingchange: { id: string };
	floorchange: { id: string | null };
	modechange: { mode: WorldMode };
	/** A space or zone chosen (by a click or `select`), or none. */
	select: { id: string | null; feature: Feature | null };
	/** The walker went into another space. */
	roomchange: { id: string | null; type: string | null; name: string | null; number: string | null; stairs: boolean };
	walklock: { locked: boolean };
}

/** A floor's plan in local metres (x east, z south), for a minimap. */
export interface WorldPlan {
	walls: [number, number][][];
	spaces: { id: string; type: string; name: string | null; rings: [number, number][][] }[];
	bounds: unknown;
}

/** A building of a package as a 3D world: the dollhouse view, or a walk through it. */
export declare class StoreyPathWorld extends EventTarget {
	/** `container`: an element or a selector. Needs WebGL 2 (see `webglSupport`). */
	constructor(container: string | HTMLElement, options?: WorldOptions);
	readonly package: StoreyPathPackage | null;
	readonly building: string | null;
	/** The floor shown on its own, or null for all. */
	readonly floor: string | null;
	readonly mode: WorldMode;
	readonly selected: string | null;
	readonly room: unknown;
	readonly walking: boolean;
	readonly atStairs: boolean;
	readonly walkFloor: string | null;
	readonly player: { x: number; z: number; dx: number; dz: number; floor: string | null };
	/** three.js objects, for anything else. */
	readonly camera: unknown;
	readonly renderer: unknown;
	readonly scene: unknown;
	/** Open a package (URL, Blob, File or ArrayBuffer) and show its first building. */
	open(source: string | URL | Blob | ArrayBuffer | Uint8Array): Promise<StoreyPathPackage>;
	setBuilding(id: string): void;
	/** Show one floor, or all with null. */
	setFloor(id: string | null): void;
	setMode(mode: WorldMode): void;
	/** In the walk view, take the mouse to look around (call from a click). */
	startWalking(): void;
	setXray(on: boolean): void;
	setCutaway(on: boolean): void;
	setLabels(on: boolean): void;
	setShowHidden(on: boolean): void;
	setExplode(meters: number): void;
	/** Highlight a space or zone (null: none); `go` (default true) takes the view to it. */
	select(id: string | null, options?: { go?: boolean }): void;
	/** Up (+1) or down (−1) a floor from where the walker stands. */
	changeFloor(step: number): boolean;
	toLocal(lonlat: LonLat): { x: number; z: number };
	plan(floorId: string): WorldPlan | null;
	/** Stop drawing and free the GPU; the container is emptied. */
	destroy(): void;
	addEventListener<K extends keyof WorldEvents>(
		type: K,
		listener: (event: CustomEvent<WorldEvents[K]>) => void,
		options?: boolean | AddEventListenerOptions
	): void;
	addEventListener(type: string, listener: EventListenerOrEventListenerObject | null, options?: boolean | AddEventListenerOptions): void;
	removeEventListener<K extends keyof WorldEvents>(
		type: K,
		listener: (event: CustomEvent<WorldEvents[K]>) => void,
		options?: boolean | EventListenerOptions
	): void;
	removeEventListener(type: string, listener: EventListenerOrEventListenerObject | null, options?: boolean | EventListenerOptions): void;
}
