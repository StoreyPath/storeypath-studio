# StoreyPath 3D world, as one package

`@storeypath/viewer-world` is the viewer's `StoreyPathWorld` (a building from a
StoreyPath package as a 3D world: the dollhouse view, or a walk through it; see
[../README.md](../README.md)) made into one ES module, with three.js and JSZip
inside it, and declarations for TypeScript. An application installs it and needs
nothing else: no import map, no CDN, nothing downloaded at run time. That is how
wayfinder shows its floors in 3D.

```ts
import { webglSupport } from "@storeypath/viewer-world/support";   // small: no three.js

if (webglSupport().ok) {
  const { StoreyPathWorld } = await import("@storeypath/viewer-world"); // ~820 kB, loaded only now
  const world = new StoreyPathWorld(element);
  await world.open(await (await fetch("/files/headquarters.storeypath")).arrayBuffer());
  world.select("K7Q2XM-RUH-HQ-F02-0142");
} else {
  // show the floor with the SVG plan viewer (../svg), made for machines with no GPU
}
```

`webglSupport()` answers `{ok, reason, renderer}`: `ok` only with WebGL 2 on a
graphics card. A browser that would give WebGL only with a major performance
caveat, or draws it in software (SwiftShader, llvmpipe, Microsoft Basic Render:
VDI desktops, kiosks with no GPU), is refused: the world does run there, at a
frame a second or so.

The module exports `StoreyPathWorld`, `loadPackage`, `StoreyPathPackage`,
`TYPE_COLORS` and `webglSupport`; `dist/world.d.ts` declares them.

## Pre-built 3D

```sh
node bake.mjs campus.storeypath out/     # out/<floor-id>.glb, one a floor
```

`bake.mjs` builds every floor of a package with the world's own builder
(`../src/world/build.js`) and writes it as binary glTF: what a package's `world/`
folder holds (format 0.5, "Pre-built 3D" in `spec/FORMAT.md`). Studio runs it when
it exports, when it finds Node.js (20.6 or newer); it needs nothing installed, as
it takes three.js and JSZip from `../vendor`. The world shows a floor pre-built
when the package has it, reading it with three.js's glTF loader (in the module),
instead of building it.

## Build and test

```sh
npm install
npm test        # build, type-check test/usage.ts, then test/run.mjs in headless Chrome
npm pack        # storeypath-viewer-world-<version>.tgz
```

`build.mjs` bundles `../src/world`, `../src/package.js` and `../src/theme.js`
with the copies in `../vendor` (three.js r186, JSZip 3.10.1); their licences
are in `dist/THIRD-PARTY-LICENSES.txt`. The tests open the conformance packages
with WebGL drawn in software (Chrome's SwiftShader), so they run on any machine.

Apache-2.0, like the rest of StoreyPath; three.js is MIT, JSZip is used under MIT.
