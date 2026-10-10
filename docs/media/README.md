# The README's pictures

`docs/images/` is taken from the running app, never drawn by hand, so it can be taken again
after a change to Studio:

```sh
node docs/media/capture.mjs              # every picture and animation
node docs/media/capture.mjs review hero  # those alone
```

- `stage.py` starts a throwaway Studio for it: a database of its own (made on
  `STOREYPATH_MEDIA_SERVER`, else the tests' PostgreSQL on 127.0.0.1:55470, and dropped
  after), the demo campus (`storeypath demo`, all of it made up and the same every time),
  two empty projects to drop a drawing into, and made-up people with passwords drawn for
  the run.
- `capture.mjs` drives it in headless Chrome drawing on the graphics card (the viewers'
  test harness, its `gpu` mode: macOS with Metal), each scene a function: what it opens,
  what it does, what it keeps. The animations are screenshots taken as fast as they come,
  put on a timeline (`clip`, `hold`), their motions taken slowly and played faster.
- `encode.py` (Pillow, from `studio/.venv`) makes the WebP pictures (lossless for Studio's
  pages, lossy for 3D), the Real and Model picture split down the middle, and the animated
  WebP.

It needs Studio's environment (`cd studio && uv sync --extra vision`), Node.js, Chrome,
and the viewer built (`npm ci && npm run build` in `storeypath-viewer/viewer/svg` and
`/world`). `--raw <folder>` keeps the screenshots as taken; `--stage <file>` uses a stage
already running (its first line saved to the file); `--out <folder>` writes elsewhere.
Keep `docs/images/` lean: about 4 MB today, the hero about 2 MB.
