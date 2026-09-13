# Vendored browser libraries

Served from `/static` rather than a CDN so the GUI draws charts with no network, and so the versions are pinned by the
repository rather than by a tag that moves. Regenerate with the URLs below.

| file | version | licence | source |
|---|---|---|---|
| `vega.min.js` | 6.4.0 | BSD-3-Clause | https://cdn.jsdelivr.net/npm/vega@6/build/vega.min.js |
| `vega-lite.min.js` | 6.4.3 | BSD-3-Clause | https://cdn.jsdelivr.net/npm/vega-lite@6/build/vega-lite.min.js |
| `vega-embed.min.js` | 7 | BSD-3-Clause | https://cdn.jsdelivr.net/npm/vega-embed@7/build/vega-embed.min.js |
| `htmx.min.js` | 2 | BSD-2-Clause | https://cdn.jsdelivr.net/npm/htmx.org@2/dist/htmx.min.js |

`vega-lite.min.js` must match the schema `renderer.build` emits (`$schema` names the major version, v6 today): Altair writes the
spec, so an Altair upgrade that moves the schema needs the bundle moved with it. `tests/test_web.py` asserts the pair agrees.
