"""Convert an explicit WGS84 GeoJSON footprint into a local metric model.

Important: GIS boundaries are not surveyed wall faces. No accuracy is implied.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

from pyproj import Transformer
from shapely.geometry import shape
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import transform


@dataclass(frozen=True)
class MetricBuilding:
    id: str
    polygons: tuple[Polygon, ...]
    epsg: int
    origin_easting_m: float
    origin_northing_m: float
    footprint_area_m2: float
    major_axis_m: float
    minor_axis_m: float
    height_m: float | None
    height_source: str | None
    source: str

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1",
            "building_id": self.id,
            "input_source": self.source,
            "coordinate_system": {"source": "EPSG:4326", "projected_epsg": self.epsg,
                                  "local_origin_easting_m": self.origin_easting_m,
                                  "local_origin_northing_m": self.origin_northing_m},
            "geometry": {"footprint_area_m2": round(self.footprint_area_m2, 4),
                         "minimum_rotated_rectangle_major_m": round(self.major_axis_m, 4),
                         "minimum_rotated_rectangle_minor_m": round(self.minor_axis_m, 4),
                         "height_m": self.height_m, "height_source": self.height_source,
                         "polygon_count": len(self.polygons)},
            "quality": {"status": "UNVERIFIED", "independent_checkpoints": 0,
                        "absolute_dimension_accuracy": "NOT_ESTABLISHED",
                        "notes": ["GIS polygon is not a measured external wall face.",
                                  "A model's metric prediction is not survey-grade evidence.",
                                  "Use independent measured check distances before design use."]},
        }


def _properties_feature(geojson: dict[str, Any], feature_id: str | None):
    kind = geojson.get("type")
    if kind == "FeatureCollection":
        features = geojson.get("features", [])
    elif kind == "Feature":
        features = [geojson]
    elif kind in ("Polygon", "MultiPolygon"):
        features = [{"type": "Feature", "id": "geometry", "properties": {}, "geometry": geojson}]
    else:
        raise ValueError("Expected a GeoJSON Polygon/MultiPolygon/Feature/FeatureCollection")
    if not features:
        raise ValueError("No features provided")
    if feature_id is None and len(features) != 1:
        raise ValueError("Multiple features: select one explicitly with --feature-id")
    if feature_id is None:
        return features[0]
    found = [f for f in features if str(f.get("id", f.get("properties", {}).get("id", ""))) == feature_id]
    if len(found) != 1:
        raise ValueError("Feature ID must resolve to exactly one building")
    return found[0]


def _utm_epsg(lon: float, lat: float) -> int:
    if not (-180 <= lon <= 180 and -80 <= lat <= 84):
        raise ValueError("Footprint outside supported UTM range")
    zone = min(60, max(1, math.floor((lon + 180) / 6) + 1))
    return (32600 if lat >= 0 else 32700) + zone


def load_metric_building(geojson: dict[str, Any], *, feature_id: str | None = None,
                         height_m: float | None = None, height_source: str | None = None,
                         source: str = "user-supplied-GeoJSON") -> MetricBuilding:
    if height_m is not None:
        if not math.isfinite(height_m) or height_m <= 0 or height_m > 1000:
            raise ValueError("height_m must be finite, >0 and <=1000 metres")
        if height_source not in ("measured", "registered", "estimated"):
            raise ValueError("height_source must be one of measured, registered, estimated")
    elif height_source is not None:
        raise ValueError("Cannot provide height_source without height_m")

    feature = _properties_feature(geojson, feature_id)
    geom = shape(feature["geometry"])
    if geom.is_empty or not geom.is_valid or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("Footprint must be a valid non-empty Polygon/MultiPolygon")
    lon, lat = float(geom.centroid.x), float(geom.centroid.y)
    epsg = _utm_epsg(lon, lat)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    projected = transform(transformer.transform, geom)
    if not projected.is_valid or projected.area <= 1e-3:
        raise ValueError("Projected footprint invalid or too small")
    x0, y0, _, _ = projected.bounds
    local = transform(lambda x, y, z=None: (x - x0, y - y0), projected)
    polys = (local,) if isinstance(local, Polygon) else tuple(local.geoms)
    # Rectangle dimensions are NOT necessarily facade width and depth.
    rect = local.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    spans = [math.dist(coords[i], coords[i + 1]) for i in range(2)]
    return MetricBuilding(
        id=str(feature.get("id", feature.get("properties", {}).get("id", "building"))),
        polygons=polys, epsg=epsg, origin_easting_m=x0, origin_northing_m=y0,
        footprint_area_m2=projected.area, major_axis_m=max(spans), minor_axis_m=min(spans),
        height_m=height_m, height_source=height_source, source=source,
    )
