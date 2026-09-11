/*
 * CityTwin placeholder map — Toronto manholes, HydroSim P2 flooding status.
 *
 * Data is self-hosted alongside this file (no ArcGIS Online dependency):
 *   data/manholes_p2.csv                    -> produced by merge_manholes_p2.py (EPSG:4326 lon/lat)
 *   data/tiles/flood_<code>/{z}/{x}/{y}.png  -> optional pre-rendered flood-depth bands per scenario
 *                                              (QGIS "Generate XYZ tiles (Directory)" with flood_bands_tile_style.qml)
 *
 * Adding a scenario = one line in SCENARIOS below (the CSV already carries all ten).
 */
require([
    "esri/Map",
    "esri/views/MapView",
    "esri/geometry/Extent",
    "esri/geometry/support/webMercatorUtils",
    "esri/widgets/BasemapToggle",
    "esri/widgets/Search",
    "esri/widgets/Legend",
    "esri/layers/CSVLayer",
    "esri/layers/WebTileLayer",
    "esri/layers/FeatureLayer"
], function (Map, MapView, Extent, webMercatorUtils, BasemapToggle, Search, Legend,
             CSVLayer, WebTileLayer, FeatureLayer) {

    // ---------------------------------------------------------------------
    // CONFIG
    // ---------------------------------------------------------------------
    const DATA_VERSION = "2026-09-11";            // bump when data files change (cache-buster)
    const MANHOLES_CSV = "data/manholes_p2.csv";

    // Scenario code = suffix of the S_/D_ columns in the CSV.
    // "flood" is optional: an XYZ tile folder of pre-rendered depth bands shown when selected.
    const SCENARIOS = [
        { code: "AUG0124", label: "August 1, 2024",  flood: "data/tiles/flood_aug0124" },
        { code: "AUG0821", label: "August 8, 2021" },
        { code: "AUG1724", label: "August 17, 2024" },
        { code: "AUG1905", label: "August 19, 2005" },
        { code: "MAY0117", label: "May 1, 2017" }
        // Return-period storms are in the CSV too (2YR, 5YR, 10YR, 50YR, 100YR) —
        // add them here if/when Steve wants them on this map.
    ];

    // City of Toronto's own public ArcGIS Server (Open Government Licence – Toronto).
    // Layer 36 = Property Boundary, layer 34 = Property Boundary 5000 (generalised).
    // Set to null to run without parcels if the service misbehaves.
    const PARCELS_URL = "https://gis.toronto.ca/arcgis/rest/services/cot_geospatial27/FeatureServer/36";

    const v = (url) => url + "?v=" + DATA_VERSION;

    // ---------------------------------------------------------------------
    // MAP / EXTENT
    // ---------------------------------------------------------------------
    const torontoExtent = webMercatorUtils.geographicToWebMercator(new Extent({
        xmin: -79.65, ymin: 43.55, xmax: -79.10, ymax: 43.88,
        spatialReference: { wkid: 4326 }
    }));

    const map = new Map({ basemap: "gray-vector" });

    // ---------------------------------------------------------------------
    // FLOOD BANDS — pre-rendered raster tiles (lazy: created the first time a scenario is selected)
    // ---------------------------------------------------------------------
    const FLOOD_LEGEND = [
        { label: "1.5 m",  color: "rgb(132,0,168)" },
        { label: "1.2 m",  color: "rgb(0,38,115)" },
        { label: "0.8 m",  color: "rgb(0,77,168)" },
        { label: "0.4 m",  color: "rgb(0,112,255)" },
        { label: "0.3 m",  color: "rgb(115,178,255)" },
        { label: "0.15 m", color: "rgb(190,210,255)" }
    ];

    const floodLayers = {};   // code -> WebTileLayer
    function getFloodLayer(sc) {
        if (!sc || !sc.flood) return null;
        if (!floodLayers[sc.code]) {
            const base = new URL(sc.flood + "/", window.location.href).href;
            const lyr = new WebTileLayer({
                urlTemplate: base + "{level}/{col}/{row}.png",
                title: "Flood depth (m) — " + sc.label,
                opacity: 0.5,
                minScale: 75000,
                visible: false,
                legendEnabled: false
            });
            // Tiles outside the zoom range rendered in QGIS simply 404 (blank); minScale 75000 ~ z13.
            lyr.when(null, function (err) {
                console.warn("Flood tiles failed for " + sc.code + ": " + err.message);
            });
            floodLayers[sc.code] = lyr;
            map.add(lyr, 0);   // bottom of the stack: below parcels and manholes
        }
        return floodLayers[sc.code];
    }

    // Small HTML legend for the flood ramp (raster tiles can't feed the Legend widget)
    const floodLegendDiv = document.getElementById("floodLegend");
    function renderFloodLegend(sc) {
        if (!sc || !sc.flood) { floodLegendDiv.style.display = "none"; return; }
        floodLegendDiv.innerHTML = "<div class='floodLegendTitle'>Flood depth — " + sc.label + "</div>" +
            FLOOD_LEGEND.map(function (b) {
                return "<div class='floodLegendRow'><span class='floodSwatch' style='background:" + b.color +
                       "'></span>" + b.label + "</div>";
            }).join("");
        floodLegendDiv.style.display = "block";
    }

    // ---------------------------------------------------------------------
    // PROPERTY PARCELS — wayfinding only, faint grey outline, never storm-coded
    // ---------------------------------------------------------------------
    let propertyLayer = null;
    if (PARCELS_URL) {
        propertyLayer = new FeatureLayer({
            url: PARCELS_URL,
            title: "Property parcels",
            outFields: [],
            minScale: 36000,
            popupEnabled: false,
            legendEnabled: false,
            renderer: {
                type: "simple",
                symbol: { type: "simple-fill", style: "none", outline: { color: [200, 200, 200], width: 0.5 } }
            }
        });
        propertyLayer.when(null, function (err) {
            console.warn("Parcels service unavailable, continuing without it: " + err.message);
            map.remove(propertyLayer);
        });
        map.add(propertyLayer);
    }

    // ---------------------------------------------------------------------
    // MANHOLES — pentagon, black hairline, zoom-based size, zoom-gated
    // ---------------------------------------------------------------------
    const PENTAGON = "M16,0 L31.22,11.06 L25.40,28.94 L6.60,28.94 L0.78,11.06 Z";
    const blackHairline = { color: [0, 0, 0], width: 0.5 };

    function pentagon(fill) {
        return { type: "simple-marker", style: "path", path: PENTAGON, size: 8, color: fill, outline: blackHairline };
    }

    const sizeVV = {
        type: "size",
        valueExpression: "$view.scale",
        stops: [
            { value: 300000, size: 3 },
            { value: 75000,  size: 4 },
            { value: 18000,  size: 8 },
            { value: 4500,   size: 10 }
        ]
    };

    const darkGrey = [89, 89, 89];
    const opColor  = [29, 216, 51];
    const flColor  = [255, 10, 33];
    const ndColor  = [160, 160, 160];

    const neutralManholeRenderer = {
        type: "simple",
        symbol: pentagon(darkGrey),
        visualVariables: [sizeVV]
    };

    function manholeRenderer(code) {
        return {
            type: "unique-value",
            field: "S_" + code,
            defaultSymbol: pentagon(ndColor),
            defaultLabel: "No model output",
            uniqueValueInfos: [
                { value: "OK",      label: "Operational", symbol: pentagon(opColor) },
                { value: "FLOODED", label: "Flooded",     symbol: pentagon(flColor) }
                // NO_DATA (and anything unexpected) falls through to defaultSymbol / defaultLabel
            ],
            visualVariables: [sizeVV]
        };
    }

    const baseFieldInfos = [
        { fieldName: "sewer_type",   label: "Sewer type" },
        { fieldName: "install_date", label: "Install date" },
        { fieldName: "top_elev",     label: "Rim elevation (m)", format: { places: 2 } },
        { fieldName: "target",       label: "Sewershed / group" }
    ];

    function makeManholePopup(sc) {
        const fieldInfos = baseFieldInfos.slice();
        const expressionInfos = [];
        if (sc) {
            expressionInfos.push({
                name: "status",
                title: "Status — " + sc.label,
                expression:
                    "var s = $feature.S_" + sc.code + ";" +
                    "When(s == 'OK', 'Operational', s == 'FLOODED', 'Flooded', 'No model output')"
            });
            expressionInfos.push({
                name: "depth",
                title: "Max flood depth — " + sc.label,
                expression:
                    "var d = $feature.D_" + sc.code + ";" +
                    "IIf(IsEmpty(d), 'No model output', Text(Round(Number(d), 2)) + ' m')"
            });
            fieldInfos.unshift({ fieldName: "expression/depth" });
            fieldInfos.unshift({ fieldName: "expression/status" });
        }
        return {
            title: "Manhole {asset_id}",
            expressionInfos: expressionInfos,
            content: [{ type: "fields", fieldInfos: fieldInfos }]
        };
    }

    const manholeLayer = new CSVLayer({
        url: v(MANHOLES_CSV),
        latitudeField: "lat",
        longitudeField: "lon",
        title: "Manholes",
        outFields: ["*"],
        minScale: 150000,
        renderer: neutralManholeRenderer,
        popupTemplate: makeManholePopup(null)
    });
    map.add(manholeLayer);

    // ---------------------------------------------------------------------
    // VIEW + WIDGETS
    // ---------------------------------------------------------------------
    const view = new MapView({
        container: "viewDiv",
        map: map,
        center: [-79.38, 43.65],
        zoom: 11,
        constraints: { geometry: torontoExtent, minZoom: 10, rotationEnabled: false }
    });

    view.ui.add(new Search({ view: view }), "top-right");
    view.ui.add(new BasemapToggle({ view: view, nextBasemap: "hybrid" }), "bottom-left");
    view.ui.add(new Legend({ view: view }), "bottom-right");   // manholes (+ parcels) legend
    view.ui.add(document.getElementById("floodLegend"), { position: "bottom-right", index: 0 });

    // ---------------------------------------------------------------------
    // SCENARIO SELECTOR (options built from SCENARIOS)
    // ---------------------------------------------------------------------
    const scenarioPanel  = document.getElementById("scenarioPanel");
    const scenarioSelect = document.getElementById("scenarioSelect");
    const scenarioStatus = document.getElementById("scenarioStatus");
    view.ui.add(scenarioPanel, { position: "top-left", index: 0 });

    SCENARIOS.forEach(function (sc) {
        const opt = document.createElement("option");
        opt.value = sc.code;
        opt.textContent = sc.label;
        scenarioSelect.appendChild(opt);
    });

    // ---------------------------------------------------------------------
    // STAT BOXES
    // ---------------------------------------------------------------------
    const statTotal       = document.getElementById("statTotal");
    const statOperational = document.getElementById("statOperational");
    const statFlooded     = document.getElementById("statFlooded");

    function setStat(el, n) { el.textContent = (n == null) ? "—" : n.toLocaleString(); }

    manholeLayer.when(function () {
        manholeLayer.queryFeatureCount().then(function (n) { setStat(statTotal, n); });
    }, function (err) {
        scenarioStatus.textContent = "Manhole data failed to load: " + err.message;
        console.error(err);
    });

    const statusCache = {};
    function updateStats(sc) {
        if (!sc) {
            setStat(statOperational, null); setStat(statFlooded, null);
            return Promise.resolve(null);
        }
        if (statusCache[sc.code]) {
            const c = statusCache[sc.code];
            setStat(statOperational, c.ok); setStat(statFlooded, c.fl);
            return Promise.resolve(c);
        }
        const f = "S_" + sc.code;
        return manholeLayer.when().then(function () {
            return Promise.all([
                manholeLayer.queryFeatureCount({ where: f + " = 'OK'" }),
                manholeLayer.queryFeatureCount({ where: f + " = 'FLOODED'" }),
                manholeLayer.queryFeatureCount({ where: f + " = 'NO_DATA'" })
            ]);
        }).then(function (r) {
            const c = { ok: r[0], fl: r[1], nd: r[2] };
            statusCache[sc.code] = c;
            setStat(statOperational, c.ok); setStat(statFlooded, c.fl);
            return c;
        });
    }

    // ---------------------------------------------------------------------
    // APPLY SCENARIO
    // ---------------------------------------------------------------------
    function applyScenario(code) {
        const sc = SCENARIOS.find(function (s) { return s.code === code; }) || null;

        // hide every flood layer, then show the selected one (if it has one)
        Object.keys(floodLayers).forEach(function (k) { floodLayers[k].visible = false; });

        renderFloodLegend(sc);

        if (!sc) {
            scenarioStatus.textContent = "No scenario selected";
            manholeLayer.renderer = neutralManholeRenderer;
            manholeLayer.popupTemplate = makeManholePopup(null);
            updateStats(null);
            return;
        }

        manholeLayer.renderer = manholeRenderer(sc.code);
        manholeLayer.popupTemplate = makeManholePopup(sc);

        const fl = getFloodLayer(sc);
        if (fl) fl.visible = true;

        scenarioStatus.textContent = "Showing: " + sc.label + (fl ? "" : " (no flood extent for this event)");
        updateStats(sc).then(function (c) {
            if (c && c.nd > 0) {
                scenarioStatus.textContent += " · " + c.nd.toLocaleString() + " manholes without model output";
            }
        });
    }

    scenarioSelect.addEventListener("change", function (e) { applyScenario(e.target.value); });
    applyScenario(scenarioSelect.value);
});
