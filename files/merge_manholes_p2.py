r"""
merge_manholes_p2.py
===========================================================================
Merge every  <target>\QGIS\Network\P2\*_manholes_P2_flooding.shp  under
Sewersheds_V2 into ONE web-ready CSV for the CityTwin placeholder manholes map
(ArcGIS Maps SDK for JS, CSVLayer).

HOW TO RUN (either works; no ArcGIS licence needed):
  A) OSGeo4W Shell:            python merge_manholes_p2.py
  B) QGIS Python console:      exec(open(r"C:\path\to\merge_manholes_p2.py", encoding="utf-8").read())

  Both environments ship the GDAL/OGR Python bindings (osgeo). If osgeo is
  missing (plain CPython), the script falls back to pyshp + pyproj:
      pip install pyshp pyproj

WHAT IT DOES
  1. Globs the P2 manholes shapefiles (pattern in CONFIG).
  2. Per file, auto-detects the base fields (asset ID, sewer type, install
     date, top elevation) since source field names vary by target. Override
     per-file in FIELD_OVERRIDES if detection guesses wrong.
  3. Reads the 20 P2 fields (D_* depth, S_* status) for the 10 scenarios.
  4. Reprojects geometry (any CRS with a .prj) to EPSG:4326 lon/lat.
  5. Normalises: status upper-cased; depth -999 sentinel -> empty (null);
     depths rounded to 3 dp; coordinates to 6 dp (~0.1 m).
  6. SURCHARGE JOIN (v2): reads the HydroSim pipe surcharge CSVs for the same
     target (<target>\HydroSim Results\*\Surcharge\<storm>_2025_0_0__surcharge.csv,
     columns "Object ID, surcharge"). Pipe ID = upstream node ID + ".N"; the
     node's value is the MAX over its outgoing pipes. Final status per Steve,
     severity-max across the two sources:
         FLOODED     if node depth > 0  OR  pipe value >= 2
         SURCHARGED  elif pipe value >= 1
         OK          elif depth status OK  OR  pipe value present (< 1)
         NO_DATA     otherwise
     Targets without surcharge tables (SS18/SS45/SS51) keep depth-only status.
  7. Dedupes by asset ID across targets: keeps the record with the fewest
     NO_DATA statuses (tie -> first seen), logs every duplicate.
  8. Writes manholes_p2.csv + manholes_p2_summary.txt (per-target and
     per-scenario counts, surcharge coverage, status transitions caused by
     the surcharge join, duplicates, field detections, warnings).

OUTPUT CSV COLUMNS (in order)
  asset_id, sewer_type, install_date, top_elev, target, lon, lat,
  then for each scenario in SCENARIOS: S_<code>, D_<code>, P_<code>
     S_ = final status (OK / SURCHARGED / FLOODED / NO_DATA)
     D_ = node flood depth (m, blank when none)
     P_ = downstream-pipe surcharge value (blank when no pipe record)

v2.1 — 2026-09-18  node-ID normaliser: strips "(SSxx ... Network)" qualifiers and "-N" split suffixes
v2 — 2026-09-18  (surcharge join)
v1 — 2026-09-11
===========================================================================
"""

import os
import re
import csv
import sys
import glob
import time
from collections import Counter, OrderedDict

# ===========================================================================
# CONFIG
# ===========================================================================

ROOT = (r"C:\Users\rapha\Noah Intelligence"
        r"\Product and Technology Development - Documents"
        r"\Old Sewershed Modelling\HydroSim\MUNI-Toronto\Sewersheds_V2")

# Glob relative to ROOT. "*" = every target folder (GROUP 1a-DRAFT, SS14 - NCRI, ...)
SHP_GLOB = os.path.join("*", "QGIS", "Network", "P2", "*_manholes_P2_flooding.shp")

OUT_DIR = os.path.join(ROOT, "LOGS", "web_manholes")
OUT_CSV = "manholes_p2.csv"
OUT_SUMMARY = "manholes_p2_summary.txt"

# Scenario codes -> the app's labels. Order here = column order in the CSV.
# All 10 are exported so the app can add/remove scenarios without re-running.
SCENARIOS = OrderedDict([
    ("2YR",     "2-year design storm"),
    ("5YR",     "5-year design storm"),
    ("10YR",    "10-year design storm"),
    ("50YR",    "50-year design storm"),
    ("100YR",   "100-year design storm"),
    ("AUG0124", "August 1, 2024"),
    ("AUG0821", "August 8, 2021"),
    ("AUG1724", "August 17, 2024"),
    ("AUG1905", "August 19, 2005"),
    ("MAY0117", "May 1, 2017"),
])

VALID_STATUS = {"OK", "FLOODED", "NO_DATA"}
SENTINEL = -999.0

# --- Surcharge tables ------------------------------------------------------
# Glob relative to each TARGET FOLDER (the folder 3 levels above the P2 shapefile).
SURCHARGE_GLOB = os.path.join("HydroSim Results", "*", "Surcharge", "*__surcharge.csv")
# Storm token in the filename -> scenario code. Matched with STORM_TOKEN_RE so
# prefixed names like "Link_SS14_NCRI_DRAFT!_2_2025_0_0__surcharge.csv" also resolve.
STORM_TOKENS = {
    "2": "2YR", "5": "5YR", "10": "10YR", "50": "50YR", "100": "100YR",
    "August012024": "AUG0124", "August082021": "AUG0821", "August172024": "AUG1724",
    "August192005": "AUG1905", "May012017": "MAY0117",
}
STORM_TOKEN_RE = re.compile(r"(?:^|[_!])(\d+|[A-Za-z]+\d{6})_2025_0_0__surcharge\.csv$", re.IGNORECASE)
PIPE_ID_RE = re.compile(r"^(.*?)\.(\d+)$")
# Node-ID normalisation applied symmetrically to pipe-derived node IDs and manhole asset IDs:
#   - strip a trailing network qualifier in parentheses:  "MH4604497321 (SS11 STM Network)" -> "MH4604497321"
#   - optionally strip a "-N" split-node suffix:            "MH4033619666-1" -> "MH4033619666"  (SS47)
QUALIFIER_RE = re.compile(r"\s*\([^()]*\)\s*$")
DASH_SUFFIX_RE = re.compile(r"-\d+$")
STRIP_DASH_SUFFIX = True   # set False if Steve wants "-1" nodes treated as distinct from the parent manhole
SURCHARGE_FLOODED = 2.0      # pipe value >= this -> FLOODED
SURCHARGE_SURCHARGED = 1.0   # pipe value >= this (and < FLOODED) -> SURCHARGED

# Targets to skip, matched case-insensitively against the product prefix
# taken from the filename ("Grp1a" from Grp1a_manholes_P2_flooding.shp).
SKIP_TARGETS = set()

# Per-file overrides when auto-detection is wrong. Key = product prefix.
# e.g. FIELD_OVERRIDES = {"SS14": {"id": "Asset_Iden", "type": "Sewer_Main"}}
FIELD_OVERRIDES = {}

# Candidate field names (case/space/underscore-insensitive prefix match).
ID_CANDIDATES   = ["asset iden", "asset_iden", "assetid", "asset_id", "asset id"]
TYPE_CANDIDATES = ["sewer main", "sewer_main", "flow type", "flow_type", "sewer type"]
DATE_CANDIDATES = ["sewer ma_1", "sewer_ma_1", "install da", "install_da", "install_date", "installed"]
ELEV_CANDIDATES = ["top elevat", "top_elevat", "top_elev", "rim elev"]

KNOWN_TYPES = {"STORM", "COMBINED", "SANITARY", "SCSO", "CSO", "SAN", "STM", "COMB"}
ID_PATTERN = re.compile(r"^(MH|CB|JP|CN|IN)\d+", re.IGNORECASE)

# Loose Toronto bounding box (EPSG:4326) for a sanity check on reprojection.
TORONTO_BBOX = (-79.70, 43.50, -79.05, 43.95)

# ===========================================================================
# READERS — one interface, two backends
#   read_shapefile(path) -> (field_names, iterator of (attrs_dict, (lon, lat) or None))
# ===========================================================================

def _norm(name):
    return re.sub(r"[\s_]+", " ", str(name)).strip().lower()


try:
    from osgeo import ogr, osr
    ogr.UseExceptions()
    BACKEND = "osgeo"
except ImportError:
    BACKEND = None

if BACKEND is None:
    try:
        import shapefile as _pyshp   # pyshp
        from pyproj import CRS as _CRS, Transformer as _Transformer
        BACKEND = "pyshp"
    except ImportError:
        sys.exit("Neither osgeo (GDAL) nor pyshp+pyproj is available. "
                 "Run inside OSGeo4W / QGIS, or: pip install pyshp pyproj")


def _first_point(coords_or_geom):
    """Return (x, y) of the first vertex; handles MULTIPOINT with 1..n points."""
    return coords_or_geom[0]


def read_shapefile_osgeo(path):
    ds = ogr.Open(path, 0)
    if ds is None:
        raise IOError("OGR could not open %s" % path)
    lyr = ds.GetLayer(0)
    defn = lyr.GetLayerDefn()
    names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]

    src_srs = lyr.GetSpatialRef()
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(4326)
    # GDAL >= 3 defaults to authority axis order (lat, lon). Force lon, lat.
    if hasattr(osr, "OAMS_TRADITIONAL_GIS_ORDER"):
        dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if src_srs is not None:
            src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    xform = osr.CoordinateTransformation(src_srs, dst_srs) if src_srs is not None else None

    def gen():
        _keep_alive = (ds, lyr)   # OGR: the DataSource must outlive the Layer or iteration segfaults/TypeErrors
        lyr.ResetReading()
        for feat in lyr:
            attrs = {n: feat.GetField(i) for i, n in enumerate(names)}
            geom = feat.GetGeometryRef()
            pt = None
            if geom is not None and not geom.IsEmpty():
                g = geom.Clone()
                if xform is not None:
                    g.Transform(xform)
                if g.GetGeometryCount() > 0:          # MULTIPOINT
                    g = g.GetGeometryRef(0)
                pt = (g.GetX(), g.GetY())
            yield attrs, pt

    return names, gen(), (src_srs is not None)


def read_shapefile_pyshp(path):
    r = _pyshp.Reader(os.path.splitext(path)[0])
    names = [f[0] for f in r.fields[1:]]
    prj = os.path.splitext(path)[0] + ".prj"
    xform = None
    has_crs = os.path.exists(prj)
    if has_crs:
        with open(prj, "r", encoding="utf-8", errors="replace") as fh:
            src = _CRS.from_wkt(fh.read())
        xform = _Transformer.from_crs(src, _CRS.from_epsg(4326), always_xy=True)

    def gen():
        for sr in r.iterShapeRecords():
            attrs = dict(zip(names, sr.record))
            pt = None
            if sr.shape.points:
                x, y = _first_point(sr.shape.points)
                if xform is not None:
                    x, y = xform.transform(x, y)
                pt = (x, y)
            yield attrs, pt

    return names, gen(), has_crs


read_shapefile = read_shapefile_osgeo if BACKEND == "osgeo" else read_shapefile_pyshp

# ===========================================================================
# FIELD DETECTION
# ===========================================================================

def _match(names, candidates):
    normed = {_norm(n): n for n in names}
    for cand in candidates:
        cand = _norm(cand)
        for nn, real in normed.items():
            if nn == cand or nn.startswith(cand):
                return real
    return None


def detect_fields(names, sample_rows, prefix, warnings):
    """Return dict with keys id/type/date/elev -> real field name or None."""
    ov = FIELD_OVERRIDES.get(prefix, {})
    det = {
        "id":   ov.get("id")   or _match(names, ID_CANDIDATES),
        "type": ov.get("type") or _match(names, TYPE_CANDIDATES),
        "date": ov.get("date") or _match(names, DATE_CANDIDATES),
        "elev": ov.get("elev") or _match(names, ELEV_CANDIDATES),
    }

    # Content-based fallbacks / validation on a sample.
    def frac(field, pred):
        vals = [row.get(field) for row in sample_rows if row.get(field) not in (None, "")]
        return (sum(1 for v in vals if pred(v)) / len(vals)) if vals else 0.0

    p2 = {n for n in names if n.upper().startswith(("D_", "S_"))}
    base = [n for n in names if n not in p2]

    if det["id"] is None or frac(det["id"], lambda v: bool(ID_PATTERN.match(str(v)))) < 0.5:
        best = max(base, key=lambda n: frac(n, lambda v: bool(ID_PATTERN.match(str(v)))), default=None)
        if best and frac(best, lambda v: bool(ID_PATTERN.match(str(v)))) >= 0.5:
            if det["id"] != best:
                warnings.append("[%s] id field chosen by content: '%s'" % (prefix, best))
            det["id"] = best
        else:
            det["id"] = None

    if det["type"] is None or frac(det["type"], lambda v: str(v).strip().upper() in KNOWN_TYPES) < 0.5:
        best = max(base, key=lambda n: frac(n, lambda v: str(v).strip().upper() in KNOWN_TYPES), default=None)
        if best and frac(best, lambda v: str(v).strip().upper() in KNOWN_TYPES) >= 0.5:
            if det["type"] != best:
                warnings.append("[%s] type field chosen by content: '%s'" % (prefix, best))
            det["type"] = best
        else:
            det["type"] = None

    if det["date"] is not None and frac(det["date"], lambda v: bool(re.match(r"^\d{4}-\d{2}-\d{2}", str(v)))) < 0.5:
        warnings.append("[%s] '%s' does not look like dates; install_date left blank" % (prefix, det["date"]))
        det["date"] = None

    return det

# ===========================================================================
# NORMALISERS
# ===========================================================================

def norm_status(v):
    if v is None:
        return "NO_DATA"
    s = str(v).strip().upper()
    return s if s in VALID_STATUS else None


def norm_depth(v):
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if abs(f - SENTINEL) < 1e-6:
        return ""
    return "%.3f" % f


def norm_date(v):
    if v is None:
        return ""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else str(v).strip()


def norm_elev(v):
    if v is None or v == "":
        return ""
    try:
        return "%.2f" % float(v)
    except (TypeError, ValueError):
        return ""


def prefix_from_filename(path):
    base = os.path.basename(path)
    m = re.match(r"^(.*?)_manholes_P2_flooding\.shp$", base, re.IGNORECASE)
    return m.group(1) if m else os.path.splitext(base)[0]

# ===========================================================================
# SURCHARGE TABLES
# ===========================================================================

def target_folder_from_shp(path):
    """<ROOT>/<target>/QGIS/Network/P2/x.shp -> <ROOT>/<target>"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(path))))


def load_surcharge_tables(target_dir, prefix, warnings):
    """
    Returns {scenario_code: {NODE_ID_UPPER: max_pipe_value}} for one target,
    plus a small stats dict. Empty dict if the target has no tables.
    """
    files = sorted(glob.glob(os.path.join(target_dir, SURCHARGE_GLOB)))
    tables = {}
    info = {"files": len(files), "unmapped": [], "rows": 0}
    if not files:
        warnings.append("[%s] no surcharge tables under %s — depth-only status" %
                        (prefix, os.path.join(target_dir, SURCHARGE_GLOB)))
        return tables, info
    for fp in files:
        m = STORM_TOKEN_RE.search(os.path.basename(fp))
        code = STORM_TOKENS.get(m.group(1)) if m else None
        if code is None:
            info["unmapped"].append(os.path.basename(fp))
            continue
        if code not in SCENARIOS:
            continue
        nodes = tables.setdefault(code, {})
        with open(fp, "r", encoding="utf-8-sig", newline="") as fh:
            rdr = csv.reader(fh)
            header = next(rdr, None)
            for row in rdr:
                if len(row) < 2:
                    continue
                pid = row[0].strip()
                pm = PIPE_ID_RE.match(pid)
                node = norm_node_id(pm.group(1) if pm else pid)
                try:
                    val = float(row[1])
                except ValueError:
                    continue
                info["rows"] += 1
                if node not in nodes or val > nodes[node]:
                    nodes[node] = val
    if info["unmapped"]:
        warnings.append("[%s] surcharge files with unrecognised storm token (ignored): %s" %
                        (prefix, info["unmapped"]))
    missing = [c for c in SCENARIOS if c not in tables]
    if missing and tables:
        warnings.append("[%s] surcharge tables missing for scenarios %s" % (prefix, missing))
    return tables, info


def norm_node_id(raw):
    """Symmetric node-ID normaliser (see QUALIFIER_RE / DASH_SUFFIX_RE)."""
    n = str(raw).strip()
    n = QUALIFIER_RE.sub("", n).strip()
    if STRIP_DASH_SUFFIX:
        n = DASH_SUFFIX_RE.sub("", n)
    return n.upper()


def combine_status(depth_status, pipe_val):
    """Severity-max of node flood depth status and downstream pipe surcharge value."""
    if depth_status == "FLOODED" or (pipe_val is not None and pipe_val >= SURCHARGE_FLOODED):
        return "FLOODED"
    if pipe_val is not None and pipe_val >= SURCHARGE_SURCHARGED:
        return "SURCHARGED"
    if depth_status == "OK" or pipe_val is not None:
        return "OK"
    return "NO_DATA"

# ===========================================================================
# MAIN
# ===========================================================================

def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    warnings = []
    lines = []

    def log(msg=""):
        print(msg)
        lines.append(msg)

    log("merge_manholes_p2.py  (backend: %s)" % BACKEND)
    log("ROOT: %s" % ROOT)
    files = sorted(glob.glob(os.path.join(ROOT, SHP_GLOB)))
    if not files:
        sys.exit("No shapefiles matched %s" % os.path.join(ROOT, SHP_GLOB))
    log("Found %d P2 manholes shapefiles" % len(files))
    log()

    status_cols = ["S_%s" % c for c in SCENARIOS]
    depth_cols  = ["D_%s" % c for c in SCENARIOS]

    records = {}            # key (upper id) -> row dict
    transitions = Counter() # (code, depth_status, final_status) -> n
    surch_coverage = OrderedDict()   # prefix -> (files, hits per scenario, manholes)
    dup_log = []
    per_target = OrderedDict()
    bad_status = Counter()
    n_read = n_no_geom = n_out_bbox = 0

    for path in files:
        prefix = prefix_from_filename(path)
        if prefix.upper() in {s.upper() for s in SKIP_TARGETS}:
            log("SKIP   %s" % prefix)
            continue

        names, rows_iter, has_crs = read_shapefile(path)
        if not has_crs:
            warnings.append("[%s] no .prj — coordinates NOT reprojected (assumed already lon/lat)" % prefix)

        missing = [c for c in status_cols + depth_cols if c not in names]
        if missing:
            warnings.append("[%s] missing P2 fields %s (filled as NO_DATA)" % (prefix, missing))

        rows = list(rows_iter)          # materialise once: detection needs a sample
        sample = [a for a, _ in rows[:200]]
        det = detect_fields(names, sample, prefix, warnings)
        surch, surch_info = load_surcharge_tables(target_folder_from_shp(path), prefix, warnings)
        surch_hits = Counter()          # per scenario: manholes with a pipe record
        if det["id"] is None:
            warnings.append("[%s] could not identify an asset-ID field — FILE SKIPPED. Fields: %s" % (prefix, names))
            log("ERROR  %s: no id field, skipped" % prefix)
            continue

        stats = Counter()
        kept = 0
        for attrs, pt in rows:
            n_read += 1
            raw_id = attrs.get(det["id"])
            if raw_id is None or str(raw_id).strip() == "":
                stats["no_id"] += 1
                continue
            asset_id = str(raw_id).strip()
            key = asset_id.upper()
            join_key = norm_node_id(asset_id)

            if pt is None:
                n_no_geom += 1
                stats["no_geom"] += 1
                continue
            lon, lat = pt
            if not (TORONTO_BBOX[0] <= lon <= TORONTO_BBOX[2] and TORONTO_BBOX[1] <= lat <= TORONTO_BBOX[3]):
                n_out_bbox += 1
                stats["out_of_bbox"] += 1

            row = OrderedDict()
            row["asset_id"] = asset_id
            row["sewer_type"] = str(attrs.get(det["type"], "")).strip() if det["type"] else ""
            row["install_date"] = norm_date(attrs.get(det["date"])) if det["date"] else ""
            row["top_elev"] = norm_elev(attrs.get(det["elev"])) if det["elev"] else ""
            row["target"] = prefix
            row["lon"] = "%.6f" % lon
            row["lat"] = "%.6f" % lat
            n_nodata = 0
            for code in SCENARIOS:
                s = norm_status(attrs.get("S_%s" % code)) if ("S_%s" % code) in names else "NO_DATA"
                if s is None:
                    bad_status[str(attrs.get("S_%s" % code))] += 1
                    s = "NO_DATA"
                d = norm_depth(attrs.get("D_%s" % code)) if ("D_%s" % code) in names else ""
                if s == "NO_DATA":
                    d = ""
                pipe_val = surch.get(code, {}).get(join_key)
                if pipe_val is not None:
                    surch_hits[code] += 1
                final = combine_status(s, pipe_val)
                transitions[(code, s, final)] += 1
                if final == "NO_DATA":
                    n_nodata += 1
                row["S_%s" % code] = final
                row["D_%s" % code] = d
                row["P_%s" % code] = ("%.2f" % pipe_val) if pipe_val is not None else ""
            row["_nodata"] = n_nodata

            if key in records:
                prev = records[key]
                # keep the more complete record; tie -> first seen
                if n_nodata < prev["_nodata"]:
                    dup_log.append("%s: replaced %s (NO_DATA %d) with %s (NO_DATA %d)" %
                                   (asset_id, prev["target"], prev["_nodata"], prefix, n_nodata))
                    records[key] = row
                else:
                    dup_log.append("%s: kept %s (NO_DATA %d), dropped %s (NO_DATA %d)" %
                                   (asset_id, prev["target"], prev["_nodata"], prefix, n_nodata))
                stats["duplicate"] += 1
                continue

            records[key] = row
            kept += 1

        per_target[prefix] = {"read": len(rows), "kept": kept, "det": det, "stats": stats}
        surch_coverage[prefix] = (surch_info["files"], surch_hits, len(rows))
        hit_str = ("surcharge: %d files, join %d/%d manholes" %
                   (surch_info["files"], max(surch_hits.values()) if surch_hits else 0, len(rows)))
        log("OK     %-8s read %6d  kept %6d  id='%s' type='%s' date='%s' elev='%s'  %s%s" % (
            prefix, len(rows), kept, det["id"], det["type"], det["date"], det["elev"], hit_str,
            ("  " + dict(stats).__repr__()) if stats else ""))

    # ---- write CSV ----
    out_csv = os.path.join(OUT_DIR, OUT_CSV)
    cols = ["asset_id", "sewer_type", "install_date", "top_elev", "target", "lon", "lat"]
    for code in SCENARIOS:
        cols += ["S_%s" % code, "D_%s" % code, "P_%s" % code]
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for row in records.values():
            w.writerow([row[c] for c in cols])
    size_mb = os.path.getsize(out_csv) / 1e6

    # ---- summary ----
    log()
    log("=" * 70)
    log("TOTAL manholes written: %d  (read %d, no-geom %d, duplicates %d, outside bbox %d)" %
        (len(records), n_read, n_no_geom, len(dup_log), n_out_bbox))
    log("CSV: %s  (%.1f MB)" % (out_csv, size_mb))
    log()
    log("Per-scenario FINAL status counts (these are the numbers the stat boxes will show):")
    log("  %-9s %-22s %8s %10s %8s %8s" % ("code", "label", "OK", "SURCHARGED", "FLOODED", "NO_DATA"))
    for code, label in SCENARIOS.items():
        c = Counter(r["S_%s" % code] for r in records.values())
        log("  %-9s %-22s %8d %10d %8d %8d" % (code, label, c["OK"], c["SURCHARGED"], c["FLOODED"], c["NO_DATA"]))
    log()
    log("Effect of the surcharge join (depth-only status -> final status), counted per target before dedupe:")
    log("  %-9s %10s %10s %10s %10s %10s" % ("code", "OK>SURCH", "OK>FLOOD", "NODATA>OK", "NODATA>SURCH", "NODATA>FLOOD"))
    for code in SCENARIOS:
        log("  %-9s %10d %10d %10d %10d %10d" % (
            code,
            transitions[(code, "OK", "SURCHARGED")], transitions[(code, "OK", "FLOODED")],
            transitions[(code, "NO_DATA", "OK")], transitions[(code, "NO_DATA", "SURCHARGED")],
            transitions[(code, "NO_DATA", "FLOODED")]))
    log()
    log("Surcharge table coverage per target (files found / manholes with a pipe record):")
    for pfx, (nfiles, hits, nrows) in surch_coverage.items():
        h = max(hits.values()) if hits else 0
        log("  %-8s %2d files   %6d / %6d manholes joined%s" % (
            pfx, nfiles, h, nrows, "" if nfiles else "   <- depth-only"))
    log()
    log("Sewer types: %s" % dict(Counter(r["sewer_type"] for r in records.values()).most_common()))
    if bad_status:
        warnings.append("Unrecognised status values coerced to NO_DATA: %s" % dict(bad_status))
    if warnings:
        log()
        log("WARNINGS (%d):" % len(warnings))
        for wmsg in warnings:
            log("  - " + wmsg)
    if dup_log:
        log()
        log("DUPLICATE asset IDs across targets (%d):" % len(dup_log))
        for d in dup_log[:50]:
            log("  " + d)
        if len(dup_log) > 50:
            log("  ... %d more" % (len(dup_log) - 50))
    log()
    log("Done in %.1f s" % (time.time() - t0))

    with open(os.path.join(OUT_DIR, OUT_SUMMARY), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        if len(dup_log) > 50:
            fh.write("\n\nFULL DUPLICATE LOG:\n" + "\n".join(dup_log))


if __name__ == "__main__" or "qgis" in sys.modules:
    main()
