r"""
diagnose_surcharge_ids.py — why did SS11/12/13/14/39/48/67 (and partly SS47) join 0 manholes?

Run in the OSGeo4W Shell or QGIS console (plain Python, no GDAL needed):
    python diagnose_surcharge_ids.py

For each target in TARGETS it prints the 2-year surcharge table's row count and first
pipe IDs, next to the first manhole IDs for that target from manholes_p2.csv.
Then it fingerprints EVERY target's 2-year table (MD5) and reports any that are identical
copies of each other — which would mean the wrong model's export was dropped in that folder.
"""
import os, re, csv, glob, hashlib
from collections import defaultdict

ROOT = (r"C:\Users\rapha\Noah Intelligence"
        r"\Product and Technology Development - Documents"
        r"\Old Sewershed Modelling\HydroSim\MUNI-Toronto\Sewersheds_V2")
MERGED_CSV = os.path.join(ROOT, "LOGS", "web_manholes", "manholes_p2.csv")

TARGETS = ["SS11", "SS12", "SS13", "SS14", "SS39", "SS47", "SS48", "SS67", "Grp1b"]   # Grp1b = known-good reference
SHP_GLOB = os.path.join("*", "QGIS", "Network", "P2", "*_manholes_P2_flooding.shp")
SURCHARGE_2YR = os.path.join("HydroSim Results", "*", "Surcharge", "*2_2025_0_0__surcharge.csv")


def prefix_from_shp(p):
    m = re.match(r"^(.*?)_manholes_P2_flooding\.shp$", os.path.basename(p), re.I)
    return m.group(1) if m else None


def target_folder(p):
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(p))))


# map prefix -> target folder via the shapefiles (same logic as the merge script)
folders = {}
for shp in glob.glob(os.path.join(ROOT, SHP_GLOB)):
    folders[prefix_from_shp(shp)] = target_folder(shp)

# manhole IDs per target from the merged CSV
mh_ids = defaultdict(list)
with open(MERGED_CSV, encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        if len(mh_ids[row["target"]]) < 5:
            mh_ids[row["target"]].append(row["asset_id"])

print("=" * 90)
for t in TARGETS:
    print("\n### %s   folder: %s" % (t, os.path.basename(folders.get(t, "?"))))
    files = glob.glob(os.path.join(folders.get(t, "?"), SURCHARGE_2YR))
    # exclude 12_/22_ etc. — we want the file whose storm token is exactly "2"
    files = [f for f in files if re.search(r"(?:^|[_!])2_2025_0_0__surcharge\.csv$", os.path.basename(f))]
    if not files:
        print("  no 2-year surcharge table found")
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        print("  table: %s  (%d rows)" % (os.path.basename(f), len(rows) - 1))
        print("  first pipe IDs : %s" % [r[0] for r in rows[1:6]])
        # rough ID shapes present
        shapes = defaultdict(int)
        for r in rows[1:]:
            shapes[re.sub(r"\d", "#", r[0].strip())] += 1
        print("  ID shapes      : %s" % sorted(shapes.items(), key=lambda kv: -kv[1])[:6])
    print("  manhole IDs    : %s" % mh_ids.get(t, []))

# fingerprint all targets' 2-year tables
print("\n" + "=" * 90)
print("Fingerprints of every target's 2-year table (identical hashes = same file copied):")
by_hash = defaultdict(list)
for t, folder in sorted(folders.items()):
    for f in glob.glob(os.path.join(folder, SURCHARGE_2YR)):
        if not re.search(r"(?:^|[_!])2_2025_0_0__surcharge\.csv$", os.path.basename(f)):
            continue
        h = hashlib.md5(open(f, "rb").read()).hexdigest()[:10]
        by_hash[h].append(t)
for h, ts in by_hash.items():
    flag = "   <-- DUPLICATE FILE across targets" if len(ts) > 1 else ""
    print("  %s  %s%s" % (h, ", ".join(ts), flag))
