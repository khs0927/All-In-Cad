"""Run GIS footprint -> DXF and optionally correctly labelled mass OBJ/IFC."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from .core import load_metric_building
from .export import write_dxf, write_obj, write_ifc
from .vworld import get_footprints


def build_parser():
    p = argparse.ArgumentParser(prog="photo-to-cad", description="Evidence-first photo/GIS-to-CAD pilot")
    s = p.add_subparsers(dest="command", required=True)
    b = s.add_parser("build", help="Convert a single WGS84 GeoJSON polygon into metric DXF and optional mass OBJ/IFC")
    b.add_argument("--geojson", type=Path, required=True)
    b.add_argument("--feature-id")
    b.add_argument("--height-m", type=float)
    b.add_argument("--height-source", choices=["measured", "registered", "estimated"])
    b.add_argument("--source", default="user-supplied-GeoJSON")
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--ifc", action="store_true", help="Requires ifcopenshell; height-m and source mandatory")
    v = s.add_parser("vworld", help="Fetch VWorld WFS features to GeoJSON; API key env only")
    v.add_argument("--typename", required=True, help="Check provider layer catalog; do not guess layer names")
    v.add_argument("--bbox", required=True, help="lon_min,lat_min,lon_max,lat_max")
    v.add_argument("--out", required=True, type=Path)
    return p


def main(argv=None):
    opts = build_parser().parse_args(argv)
    if opts.command == "vworld":
        key = os.getenv("VWORLD_API_KEY")
        if not key:
            raise SystemExit("VWORLD_API_KEY environment variable is required")
        bbox = tuple(map(float, opts.bbox.split(",")))
        if len(bbox) != 4:
            raise SystemExit("bbox requires 4 decimal numbers")
        data = get_footprints(key=key, typename=opts.typename, bbox=bbox)
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {len(data['features'])} features; choose an explicit building feature ID")
        return 0
    content = opts.geojson.read_bytes()
    data = json.loads(content)
    model = load_metric_building(data, feature_id=opts.feature_id, height_m=opts.height_m,
                                  height_source=opts.height_source, source=opts.source)
    opts.out.mkdir(parents=True, exist_ok=True)
    created = [write_dxf(model, opts.out / "footprint.dxf")]
    if model.height_m is not None:
        created.append(write_obj(model, opts.out / "mass.obj"))
        if opts.ifc:
            created.append(write_ifc(model, opts.out / "mass.ifc"))
    report = model.report()
    report["input_sha256"] = hashlib.sha256(content).hexdigest()
    report["outputs"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in created}
    (opts.out / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"files": [p.name for p in created], "quality": report["quality"]["status"],
                      "area_m2": report["geometry"]["footprint_area_m2"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
