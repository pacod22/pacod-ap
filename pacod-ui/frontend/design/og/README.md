# Share-image and icon sources

The PNGs in `static/` are generated from the HTML in this directory — edit these,
then re-run the command below. They are committed so the app ships no runtime
image-rendering dependency.

Sizes are fixed by the platforms, not by us:

- `og-*.png` — 1200x630, the Open Graph / Twitter-card ratio. Facebook, WhatsApp,
  LinkedIn and X all read this one. Social scrapers do **not** accept SVG, which
  is why these are raster.
- `icon-512.png` / `icon-192.png` — PWA manifest icons.
- `apple-touch-icon.png` — 180x180, iOS home screen.

Regenerate (macOS, headless Chrome — no npm dependency):

```sh
cd pacod-ui/frontend
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
render() { "$CHROME" --headless --disable-gpu --hide-scrollbars \
    --screenshot="static/$2" --window-size="$3" "file://$PWD/design/og/$1"; }

render og-pacod.html   og-pacod.png        1200,630
render icon-pacod.html icon-512.png        512,512
render icon-pacod.html icon-192.png        192,192
render icon-pacod.html apple-touch-icon.png 180,180
```

Colours are copied from the live design tokens — the teal from
`src/routes/+page.svelte` (`--teal` / `--teal-deep`). Keep them in step if those
change.

These are deliberate placeholders. Real artwork can replace the PNGs without a
code change — or point `SITE_OG_IMAGE` at a different path entirely.
