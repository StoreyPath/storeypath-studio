// Type-checked by `npm run check`: the declarations describe the module as a
// strict TypeScript application uses it.
import { FINISHES, StoreyPathWorld, finishOf, floorFinish, isItemId, loadPackage, normalizeItemId, route, webglSupport, type Feature,
	type Finish, type GivenItem, type Route, type WorldEvents, type WorldLook, type WorldPoint, type WorldQuality } from '@storeypath/viewer-world';
import { webglSupport as check } from '@storeypath/viewer-world/support';

export async function show(element: HTMLElement, data: ArrayBuffer): Promise<string | null> {
	const support = check();
	if (!support.ok || !webglSupport().ok) return support.reason ?? null;
	const world = new StoreyPathWorld(element, { labels: true, explode: 0, style: 'real', quality: 'auto' });
	// the look and quality: one click each (nothing is built again)
	world.addEventListener('lookchange', (e) => {
		const look: WorldLook = e.detail;
		console.log(look.style, look.quality, look.drawn, look.why ?? 'as asked');
	});
	const quality: WorldQuality = world.look.drawn === 'low' ? 'low' : 'auto';
	world.setStyle('model');
	world.setQuality(quality);
	const pkg = await world.open(data);
	await world.ready();
	const office: Feature | undefined = pkg.units.find((u) => u.properties['type'] === 'office');
	world.addEventListener('select', (e) => {
		const detail: WorldEvents['select'] = e.detail;
		console.log(detail.id, detail.feature?.properties['number']);
	});
	world.setMode('dollhouse');
	await world.setBuilding(pkg.buildings[0]!.id);
	const floor = pkg.floorsOf(pkg.buildings[0]!.id)[0]?.id ?? null;
	const prebuilt: boolean = floor !== null && pkg.hasWorld(floor) && world.prebuilt.includes(floor);
	const walls: number = floor ? (world.plan(floor)?.obstacles.length ?? 0) : 0;
	console.log(prebuilt, walls);
	world.setFloor(floor);
	world.select(office?.id ?? null, { go: true });
	// editing on top of it: what is under the pointer, a ghost, a floor's items replaced
	// a room's finishes (format 0.9): painted where the pointer is, a wall on the side aimed at
	const navy: Finish | null = finishOf('FLOOR-CARPET-NAVY');
	const walls: string = FINISHES.defaults.wall['office'] ?? FINISHES.exterior;
	world.addEventListener('pick', (e) => {
		const p: WorldPoint = e.detail;
		if (p.wall && p.room) world.updateSpace(p.room, { wall_finish: walls });
		else if (p.space && navy) world.updateSpace(p.space, { floor_finish: navy.code });
		const shown: string = floorFinish(office?.properties ?? null);
		console.log(shown, world.finishOf(p.space ?? '')?.wall);
		if (p.local && floor) {
			e.preventDefault();
			const tag: string | null = normalizeItemId('7k2q xm9f 4dp'); // as typed: 7K2Q-XM9F-4DP
			const desk: GivenItem = { id: isItemId(tag) ? tag : '7K2Q-XM9F-4DP', type: 'DESK-JUNIOR', x: p.local[0], y: p.local[1], rotation: 90 };
			world.setFloorItems(floor, [desk]);
		}
	});
	world.addEventListener('itemdrag', (e) => {
		const at: [number, number] | null = e.detail.local;
		if (at) world.ghost({ type: 'DESK-JUNIOR', x: at[0], y: at[1], ok: e.detail.altKey, guides: [[at, [at[0] + 1, at[1]]]] });
	});
	world.setDraggable(true);
	const under: WorldPoint | null = world.pointAt(10, 10);
	const back: [number, number] | null = under?.x != null && under.z != null ? world.buildingPoint({ x: under.x, z: under.z }) : null;
	console.log(back, world.worldPoint([0, 0])?.x, world.target.x, world.paused, world.draggable);
	world.updateSpace(office?.id ?? '', { name: 'Board room', type: 'meeting_room' });
	world.setMode('walk', { at: world.target, heading: 0 });
	world.stopWalking();
	world.setMode('dollhouse', { back: true });
	world.pause();
	world.resume();
	await world.reload(data, { floors: floor ? [floor] : [] });
	// the way from the first kiosk to the office, without stairs (format 0.8)
	const kiosk = pkg.items.find((i) => i.properties.type === 'KIOSK');
	const way: Route | null = pkg.navigation && kiosk && office ? route(pkg, kiosk.id, office.id, { accessible: true }) : null;
	if (way) {
		console.log(way.steps.map((s) => s.text).join('; '), way.metres, way.changes[0]?.by);
		await world.showRoute(way, { fly: false });
		await world.flyRoute({ seconds: 8 });
		world.clearRoute();
	}
	const again = await loadPackage(new Blob([data]));
	world.destroy();
	return again.project.id;
}
