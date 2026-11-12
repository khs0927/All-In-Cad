"""VWorld WFS adapter. Requires user's approved API key and verified layer name.

Not a web scraper; never caches API key in output files.
"""
import json
from urllib.parse import urlencode
from urllib.request import urlopen, Request

WFS_ENDPOINT = "https://api.vworld.kr/req/wfs"


def build_wfs_url(*, key: str, typename: str, bbox: tuple[float, float, float, float],
                  max_features: int = 30) -> str:
    if not key or not typename or not all(c.isascii() and (c.isalnum() or c in "_-:") for c in typename):
        raise ValueError("VWorld key and a valid, verified layer name are required")
    if not (1 <= max_features <= 100):
        raise ValueError("max_features must be 1..100")
    lon1, lat1, lon2, lat2 = bbox
    if not (-180 <= lon1 < lon2 <= 180 and -90 <= lat1 < lat2 <= 90):
        raise ValueError("bbox must be lon_min,lat_min,lon_max,lat_max WGS84")
    if lon2-lon1 > 0.1 or lat2-lat1 > 0.1:
        raise ValueError("Refusing wide-area WFS query; choose a small building vicinity")
    q = {"service": "WFS", "request": "GetFeature", "version": "1.1.0",
         "typename": typename, "bbox": ",".join(str(x) for x in bbox),
         "srsName": "EPSG:4326", "output": "application/json",
         "maxFeatures": max_features, "key": key}
    return f"{WFS_ENDPOINT}?{urlencode(q)}"


def get_footprints(*, key: str, typename: str, bbox: tuple[float,float,float,float]) -> dict:
    url = build_wfs_url(key=key, typename=typename, bbox=bbox)
    with urlopen(Request(url, headers={"Accept":"application/json","User-Agent":"photo-to-cad/0.1"}), timeout=15) as resp:
        raw = resp.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError("VWorld response over 5 MB")
    data = json.loads(raw)
    if data.get("type") != "FeatureCollection":
        raise ValueError("VWorld API returned no supported GeoJSON FeatureCollection; check API/layer/CRS")
    return data
