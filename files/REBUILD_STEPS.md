# Manholes map rebuild — run-book

Target repo layout (the one Netlify deploys):

```
index.html
app.js
flood_bands_tile_style.qml   (not deployed, kept for re-renders)
data/
  manholes_p2.csv                     <- step 1
  tiles/flood_aug0124/{z}/{x}/{y}.png <- step 2 (optional, August 1 2024 only)
```

## 1. Build the manholes CSV

OSGeo4W Shell (or QGIS Python console via `exec(open(r"...merge_manholes_p2.py", encoding="utf-8").read())`):

```
python merge_manholes_p2.py
```

Output: `Sewersheds_V2\LOGS\web_manholes\manholes_p2.csv` and `manholes_p2_summary.txt`.

The script also reads each target's HydroSim surcharge tables
(`<target>\HydroSim Results\*\Surcharge\<storm>_2025_0_0__surcharge.csv`) and combines them with the
node flood depth (severity-max: depth>0 or pipe=2 → FLOODED; pipe=1 → SURCHARGED; else OK). Targets with
no tables (SS18, SS45, SS51) keep depth-only status and are flagged in the coverage list.

Read the summary before deploying:
- one `OK` line per target with the detected `id / type / date / elev` fields — if any target shows `None` for id it was skipped; add an entry to `FIELD_OVERRIDES`
- the `surcharge: N files, join X/Y manholes` figure per target — 10 files expected; X/Y is the share of manholes with a downstream pipe record
- the per-scenario `OK / SURCHARGED / FLOODED / NO_DATA` table = exactly what the stat boxes will show
- the transitions table shows how many manholes the surcharge join moved out of OK — expect operational counts to drop
- the duplicate list: manholes present in two targets; the more complete record wins
- `outside bbox` should be 0 — if not, a target's `.prj` is wrong

Expected size ~25 MB for ~100k manholes (Grp1a alone was 0.5 MB for 1,986 with the P_ columns).

## 2. Render the August 2024 flood bands to tiles (QGIS, no licence needed)

Source layer (chosen by profile_flood_candidates.py — lowest vertices, single-part, went to Yang):
`smooth_and_simplify.gdb › august2024_storm_complete_smoothed_30m_simplified_zhouJones_10m_15m_erased_singleParts`

1. New empty QGIS project. Set project CRS to EPSG:3857 (Project → Properties → CRS). No basemap.
2. Layer → Add Layer → Add Vector Layer → Source type *Directory*, type *OpenFileGDB*, pick
   `smooth_and_simplify.gdb`, then choose the feature class above.
3. Layer Properties → Symbology → Style ▾ → Load Style → `flood_bands_tile_style.qml`.
   You should see 7 categories (1.5 … 0.15, plus 0.1 sharing the lightest colour), solid fills, no outline.
   Fallback if the QML won't load — categorize on `max_depth` by hand:
   1.5 = 132,0,168 · 1.2 = 0,38,115 · 0.8 = 0,77,168 · 0.4 = 0,112,255 · 0.3 = 115,178,255 · 0.15 and 0.1 = 190,210,255
   Stroke: no pen. Layer opacity 100 % (the app applies 50 %).
4. Make sure this is the ONLY visible layer — the tiler renders the whole map canvas.
5. Processing Toolbox → *Raster tools → Generate XYZ tiles (Directory)*:
   - Extent: **Calculate from layer** → the flood layer
   - Minimum zoom 12, Maximum zoom 17   (z17 ≈ 1:4,500; minScale in the app is 75,000 ≈ z13)
   - DPI 96, Background colour: transparent (default), Tile format PNG, Quality 75
   - Metatile size 4 (faster), Tile width/height 256, leave "Use inverted tile Y axis" **unchecked**
   - Output directory: `<repo>\data\tiles\flood_aug0124`
   - Leave "Output html" empty
   Run. Expect roughly 40–60k small PNGs (transparent ones are ~100 bytes). If z17 makes the folder
   unreasonably large, re-run with max zoom 16 and accept slightly soft bands at street level.
6. Sanity check: open `data\tiles\flood_aug0124\14\` — folders named ~4550–4570 (x) containing
   PNGs named ~5980–6010 (y). Any tile over a flooded ravine should show blue when opened.

Because the app's `WebTileLayer` reads `{z}/{x}/{y}.png`, no code changes are needed beyond the
`SCENARIOS` entry that already points at `data/tiles/flood_aug0124`.

## 3. Test locally

`CSVLayer`/`WebTileLayer` fetch over HTTP, so `file://` will not work:

```
cd <repo>
python -m http.server 8000
```
Open http://localhost:8000. Check: total count appears; each scenario recolours manholes and updates
the two counts; August shows flood bands (and the small depth legend) when zoomed past ~1:75k;
popups show status + depth; parcels appear
when zoomed to street level (if the City of Toronto service loads — otherwise remove nothing, the app
drops the layer itself and logs a warning in the console).

## 4. Deploy

```
git add index.html app.js flood_bands_tile_style.qml data/
git commit -m "Rebuild manholes map on P2 flooding data, self-hosted"
git push
```
Netlify redeploys; the embed URL in CityTwin is unchanged. Bump `DATA_VERSION` in `app.js`
whenever the data files change so browsers don't serve a cached copy.

## Known caveats to tell Chris before the demo
- Only August 1 2024 has flood bands; the other four events show manhole status only.
- "No model output" (grey) = targets Steve hasn't exported yet (SS18/SS45/SS51, SS59) and
  nodes absent from a scenario's CSV.
- Some depths are implausible (57 m in Grp1a) — they display as flooded, which is correct per the
  contract, but a popup will show the number.
