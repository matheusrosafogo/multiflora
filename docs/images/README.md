# README images

This folder holds the images embedded by the project READMEs. Keep the file
names exactly as listed — the READMEs reference them by relative path.

| File | Referenced by | Content |
| --- | --- | --- |
| `gui-overview.png` | Root `README.md` | Main window (queues, devices tree and terminal) |
| `gui-execution.png` | Root `README.md` | A queue running in dry-run/deploy mode |
| `containerlab-topology.png` | Root `README.md` | The demo labs after `make exec` |

Extra captures can be dropped here later and embedded with the usual
`![...](../docs/images/<name>.png)` block; the intended names (see the
comments left in the sub-READMEs) are `backend-log.png`, `render-output.png`,
`deployment-results.png` and `devcontainer-vscode.png`.

> Note: `docs/` is also the output directory of `make docs` (pdoc); this
> `images/` subfolder is never touched by the documentation build.
