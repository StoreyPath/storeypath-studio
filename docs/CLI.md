# The command line

Everything the web app does to a drawing can be done from the command line too, on a
workspace file (`*.spproj`, a project's working file). The `storeypath` command is in
the Studio image (`docker run --rm -v "$PWD:/data" ghcr.io/storeypath/studio <command>`,
or `docker exec storeypath storeypath <command>` beside a running Studio) and, from a
clone, `uv run storeypath <command>` in `studio/`.

```sh
storeypath views house.dwg                                   # the plans on the sheets, and their floors
storeypath new house.spproj --name "House"                   # a project
storeypath add-location house.spproj HOME --name "Home"
storeypath add-building house.spproj <code>-HOME VILLA --name "Villa"
storeypath add-floor house.spproj <code>-HOME-VILLA house.dwg --ordinal 0 --view "ground floor"
storeypath convert house.spproj                              # read the drawings; keeps existing IDs
storeypath list house.spproj --review                        # what needs a look, and why
storeypath export house.spproj --building VILLA -o villa.storeypath     # its item types: those its items use
storeypath export house.spproj --building VILLA -o villa.storeypath --item-types all  # or every one
storeypath validate villa.storeypath
```

| | Commands |
|---|---|
| A project and its drawings | `new`, `save-as-new`, `add-location`, `add-building`, `add-floor` (`--view`, `--units`, `--region`), `views`, `align`, `levels`, `place` |
| Reading them | `convert` (`--force`, `--no-model`, `--no-vision`, `--no-symbols`), `list` (`--review`), `fix`, `profiles` (the layer-mapping profiles) |
| Checking and completing | `review` (the review editor, on this computer alone) |
| Packages | `export --building` (`--item-types used\|all`), `validate`, `view`, `item-id`, `schema` |
| The web app | `serve` (`--data`, `--host`, `--port`, `--allowed-host`, `--cert`/`--key`, `--http`, `--secure-cookies`, `--trusted-proxy`) |
| Accounts and the database | `users` (`add`, `list`, `passwd`, `disable`, `enable`, `role`), `backup`, `restore`, `db` (`url`, `migrate`, `import`) |
| Privacy | `private` (a copy of a drawing without its private information), `words` (every word left in it) |
| Area samples | `sample make`, `sample inspect`, `sample replay` ([Area samples](AREA-SAMPLES.md)) |
| Trying it out | `demo <folder>` (the demo campus as files), `demo --studio` (into Studio's database, as a project) |

`storeypath <command> --help` gives every option; each command, one line apiece, with
the workflow and every setting: [studio/README.md](../studio/README.md#commands).
