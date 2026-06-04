require([
    "esri/Map",
    "esri/views/MapView",
    "esri/geometry/Extent",
    "esri/geometry/support/webMercatorUtils",
    "esri/widgets/BasemapToggle",
    "esri/widgets/Search",
    "esri/widgets/Legend",
    "esri/layers/FeatureLayer"
], function (Map, MapView, Extent, webMercatorUtils, BasemapToggle, Search, Legend, FeatureLayer) {

    const torontoExtent = webMercatorUtils.geographicToWebMercator(new Extent({
        xmin: -79.65, ymin: 43.55, xmax: -79.10, ymax: 43.88,
        spatialReference: { wkid: 4326 }
    }));

    const map = new Map({ basemap: "gray-vector" });

    // ---- Flood band renderers (Aug floor 0.15 m, Sep floor 0.1 m; identical colours) ----
    const floodAugRenderer = {
        type: "unique-value",
        field: "max_depth",
        uniqueValueInfos: [
            { value: 1.5,  label: "1.5 m",  symbol: { type: "simple-fill", color: [132, 0, 168],  outline: { width: 0 } } },
            { value: 1.2,  label: "1.2 m",  symbol: { type: "simple-fill", color: [0, 38, 115],   outline: { width: 0 } } },
            { value: 0.8,  label: "0.8 m",  symbol: { type: "simple-fill", color: [0, 77, 168],   outline: { width: 0 } } },
            { value: 0.4,  label: "0.4 m",  symbol: { type: "simple-fill", color: [0, 112, 255],  outline: { width: 0 } } },
            { value: 0.3,  label: "0.3 m",  symbol: { type: "simple-fill", color: [115, 178, 255], outline: { width: 0 } } },
            { value: 0.15, label: "0.15 m", symbol: { type: "simple-fill", color: [190, 210, 255], outline: { width: 0 } } }
        ]
    };

    const floodSepRenderer = {
        type: "unique-value",
        field: "max_depth",
        uniqueValueInfos: [
            { value: 1.5,  label: "1.5 m",  symbol: { type: "simple-fill", color: [132, 0, 168],  outline: { width: 0 } } },
            { value: 1.2,  label: "1.2 m",  symbol: { type: "simple-fill", color: [0, 38, 115],   outline: { width: 0 } } },
            { value: 0.8,  label: "0.8 m",  symbol: { type: "simple-fill", color: [0, 77, 168],   outline: { width: 0 } } },
            { value: 0.4,  label: "0.4 m",  symbol: { type: "simple-fill", color: [0, 112, 255],  outline: { width: 0 } } },
            { value: 0.3,  label: "0.3 m",  symbol: { type: "simple-fill", color: [115, 178, 255], outline: { width: 0 } } },
            { value: 0.1,  label: "0.1 m",  symbol: { type: "simple-fill", color: [190, 210, 255], outline: { width: 0 } } }
        ]
    };

    const floodAug = new FeatureLayer({
        url: "https://services1.arcgis.com/KsnB2VOAvO5LjdB4/arcgis/rest/services/toronto_aug_1_2024_storm_merged_single/FeatureServer/23",
        title: "Flood depth (m)",
        outFields: ["max_depth"],
        renderer: floodAugRenderer,
        opacity: 0.5,
        minScale: 75000,
        visible: false
    });
    map.add(floodAug);

    const floodSep = new FeatureLayer({
        url: "https://services1.arcgis.com/KsnB2VOAvO5LjdB4/arcgis/rest/services/sept_1948_storm_complete/FeatureServer/86",
        title: "Flood depth (m)",
        outFields: ["max_depth"],
        renderer: floodSepRenderer,
        opacity: 0.5,
        minScale: 75000,
        visible: false
    });
    map.add(floodSep);

    // ---- Property parcels: wayfinding only, faint grey outline, never storm-coded ----
    const propertyLayer = new FeatureLayer({
        url: "https://services1.arcgis.com/KsnB2VOAvO5LjdB4/arcgis/rest/services/Toronto_Municipality_Overview_Map_Demo1/FeatureServer/1",
        title: "Property parcels",
        outFields: ["ADDRESS"],
        minScale: 36000,
        renderer: {
            type: "simple",
            symbol: { type: "simple-fill", style: "none", outline: { color: [200, 200, 200], width: 0.5 } }
        }
    });
    propertyLayer.labelingInfo = [{
        labelExpressionInfo: {
            expression: `
                var addr = Trim($feature.ADDRESS);
                if (IsEmpty(addr) || addr == 'None None') { return ''; }
                return addr;
            `
        },
        symbol: {
            type: "text",
            color: [50, 50, 50],
            haloColor: [255, 255, 255],
            haloSize: 1,
            font: { size: 9, family: "sans-serif" }
        },
        labelPlacement: "always-horizontal",
        minScale: 1500,
        maxScale: 0
    }];
    propertyLayer.labelsVisible = true;
    map.add(propertyLayer);

    // ---- Manhole symbology: pentagon, black hairline, zoom-based size, zoom-gated ----
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
            { value: 18000,  size: 9 },
            { value: 4500,   size: 14 }
        ]
    };

    const darkGrey = [89, 89, 89];
    const opColor  = [29, 216, 51];
    const suColor  = [255, 198, 30];
    const flColor  = [255, 10, 33];

    const neutralManholeRenderer = {
        type: "simple",
        symbol: pentagon(darkGrey),
        visualVariables: [sizeVV]
    };

    function manholeRenderer(field) {
        return {
            type: "unique-value",
            field: field,
            defaultSymbol: pentagon(darkGrey),
            uniqueValueInfos: [
                { value: "Operational", label: "Operational", symbol: pentagon(opColor) },
                { value: "Surcharged",  label: "Surcharged",  symbol: pentagon(suColor) },
                { value: "Flooded",     label: "Flooded",     symbol: pentagon(flColor) }
            ],
            visualVariables: [sizeVV]
        };
    }
    const rendererAug = manholeRenderer("condition_100yr_aug");
    const rendererSep = manholeRenderer("condition_50yr_sep");

    function makeManholePopup(conditionField) {
        const fieldInfos = [
            { fieldName: "flow_type", label: "Flow type" },
            { fieldName: "install_date", label: "Install date" }
        ];
        if (conditionField) {
            fieldInfos.push({ fieldName: conditionField, label: "Condition" });
        }
        return { title: "Manhole {asset_id}", content: [{ type: "fields", fieldInfos: fieldInfos }] };
    }
    const manholePopupNone = makeManholePopup(null);
    const manholePopupAug  = makeManholePopup("condition_100yr_aug");
    const manholePopupSep  = makeManholePopup("condition_50yr_sep");

    const manholeLayer = new FeatureLayer({
        url: "https://services1.arcgis.com/KsnB2VOAvO5LjdB4/arcgis/rest/services/Muni_Toronto_Manholes_50yr_100yr/FeatureServer/1",
        title: "Manholes",
        outFields: ["asset_id", "flow_type", "install_date", "condition_100yr_aug", "condition_50yr_sep"],
        minScale: 150000,
        renderer: neutralManholeRenderer
    });
    map.add(manholeLayer);

    const view = new MapView({
        container: "viewDiv",
        map: map,
        center: [-79.38, 43.65],
        zoom: 11,
        constraints: { geometry: torontoExtent, minZoom: 10, rotationEnabled: false }
    });

    view.ui.add(new Search({ view: view }), "top-right");
    view.ui.add(new BasemapToggle({ view: view, nextBasemap: "hybrid" }), "bottom-left");
    view.ui.add(new Legend({
        view: view,
        layerInfos: [
            { layer: manholeLayer },
            { layer: floodAug },
            { layer: floodSep },
            { layer: propertyLayer }
        ]
    }), "bottom-right");

    // ---- Scenario selector ----
    const scenarioPanel  = document.getElementById("scenarioPanel");
    const scenarioSelect = document.getElementById("scenarioSelect");
    const scenarioStatus = document.getElementById("scenarioStatus");
    view.ui.add(scenarioPanel, { position: "top-left", index: 0 });

    const labels = { a: "August 1, 2024", b: "September 18, 1948" };

    // ---- Stat boxes ----
    const statTotal       = document.getElementById("statTotal");
    const statOperational = document.getElementById("statOperational");
    const statSurcharged  = document.getElementById("statSurcharged");
    const statFlooded     = document.getElementById("statFlooded");

    function setStat(el, n) { el.textContent = (n == null) ? "—" : n.toLocaleString(); }

    manholeLayer.queryFeatureCount().then(function (n) { setStat(statTotal, n); });

    const statusCache = {};
    function updateStats(field) {
        if (!field) {
            setStat(statOperational, null); setStat(statSurcharged, null); setStat(statFlooded, null);
            return;
        }
        if (statusCache[field]) {
            const c = statusCache[field];
            setStat(statOperational, c.op); setStat(statSurcharged, c.su); setStat(statFlooded, c.fl);
            return;
        }
        Promise.all([
            manholeLayer.queryFeatureCount({ where: field + " = 'Operational'" }),
            manholeLayer.queryFeatureCount({ where: field + " = 'Surcharged'" }),
            manholeLayer.queryFeatureCount({ where: field + " = 'Flooded'" })
        ]).then(function (r) {
            statusCache[field] = { op: r[0], su: r[1], fl: r[2] };
            setStat(statOperational, r[0]); setStat(statSurcharged, r[1]); setStat(statFlooded, r[2]);
        });
    }

    function applyScenario(scenario) {
        scenarioStatus.textContent =
            (scenario === "none") ? "No scenario selected" : "Showing: " + labels[scenario];

        if (scenario === "none") {
            manholeLayer.renderer = neutralManholeRenderer;
            manholeLayer.popupTemplate = manholePopupNone;
            floodAug.visible = false;
            floodSep.visible = false;
            updateStats(null);
        } else if (scenario === "a") {
            manholeLayer.renderer = rendererAug;
            manholeLayer.popupTemplate = manholePopupAug;
            floodAug.visible = true;
            floodSep.visible = false;
            updateStats("condition_100yr_aug");
        } else {  // "b" - September
            manholeLayer.renderer = rendererSep;
            manholeLayer.popupTemplate = manholePopupSep;
            floodAug.visible = false;
            floodSep.visible = true;
            updateStats("condition_50yr_sep");
        }
    }

    scenarioSelect.addEventListener("change", (e) => applyScenario(e.target.value));
    applyScenario(scenarioSelect.value);

});