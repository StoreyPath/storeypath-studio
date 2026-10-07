// Type-checked by `npm run check`: the declarations describe the module as a
// strict TypeScript application uses it.
import { StoreyPathWorld, loadPackage, webglSupport, type Feature, type WorldEvents } from '@storeypath/viewer-world';
import { webglSupport as check } from '@storeypath/viewer-world/support';

export async function show(element: HTMLElement, data: ArrayBuffer): Promise<string | null> {
	const support = check();
	if (!support.ok || !webglSupport().ok) return support.reason ?? null;
	const world = new StoreyPathWorld(element, { labels: true, explode: 0 });
	const pkg = await world.open(data);
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
	const again = await loadPackage(new Blob([data]));
	world.destroy();
	return again.project.id;
}
