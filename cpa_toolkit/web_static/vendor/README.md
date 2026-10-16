# Vendored third-party JS

`plotly-2.35.2.min.js` -- [Plotly.js](https://plotly.com/javascript/), MIT
licensed (see `plotly.min.js.LICENSE.txt` alongside it, bundled by upstream's
own build). Fetched from `https://cdn.plot.ly/plotly-2.35.2.min.js` on
2026-10-14 and committed here, not loaded from a CDN at runtime, so the GUI
keeps working on a bench with no internet access -- the same reasoning
`web_gui.py`'s own docstring gives for using only the Python standard
library server-side.

To update: download a newer `plotly-X.Y.Z.min.js` from `cdn.plot.ly`,
replace this file, and update the `<script src=...>` version in
`web_static/index.html` to match -- there is no build step or package
manager involved.
