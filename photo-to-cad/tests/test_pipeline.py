import hashlib
import json
from pathlib import Path
import pytest
import ezdxf
from shapely.geometry import Polygon

from photo_to_cad.core import load_metric_building
from photo_to_cad.export import write_dxf, write_obj, write_ifc
from photo_to_cad.vworld import build_wfs_url
from photo_to_cad.inference import mapanything_command, colmap_commands
from photo_to_cad.cli import main

EX = Path(__file__).resolve().parents[1] / "examples" / "footprint.geojson"


def sample():
    return json.loads(EX.read_text())


def test_metric_building_and_dxf(tmp_path):
    model = load_metric_building(sample(), height_m=9.5, height_source="estimated", source="example")
    assert model.epsg == 32652
    assert 80 < model.footprint_area_m2 < 150
    assert model.major_axis_m > model.minor_axis_m > 5
    dxf = write_dxf(model, tmp_path / "floorplan.dxf")
    doc = ezdxf.readfile(dxf)
    assert doc.header["$INSUNITS"] == 4
    polys = list(doc.modelspace().query("LWPOLYLINE"))
    assert len(polys) == 1 and polys[0].closed
    assert all(abs(float(pt[0])) < 100000 for pt in polys[0].get_points())
    assert model.report()["quality"]["status"] == "UNVERIFIED"
    assert model.report()["geometry"]["height_source"] == "estimated"


def test_obj_and_ifc_optional(tmp_path):
    model = load_metric_building(sample(), height_m=12, height_source="measured")
    f = write_obj(model, tmp_path / "mass.obj")
    content = f.read_text()
    assert "v " in content and "f " in content
    assert "12.000000" in content
    try:
        import ifcopenshell
    except ImportError:
        with pytest.raises(RuntimeError, match="ifcopenshell"):
            write_ifc(model, tmp_path / "mass.ifc")
    else:
        path = write_ifc(model, tmp_path / "mass.ifc")
        d = ifcopenshell.open(str(path))
        assert len(d.by_type("IfcBuildingElementProxy")) == 1


def test_no_height_no_3d(tmp_path):
    model = load_metric_building(sample())
    assert model.report()["geometry"]["height_m"] is None
    with pytest.raises(ValueError, match="height"):
        write_obj(model, tmp_path / "bad.obj")
    with pytest.raises(ValueError, match="height"):
        write_ifc(model, tmp_path / "bad.ifc")


def test_multiple_features_require_selection():
    data = sample()
    data["features"].append({**data["features"][0], "id": "SYNTHETIC_002"})
    with pytest.raises(ValueError, match="Multiple features"):
        load_metric_building(data)
    assert load_metric_building(data, feature_id="SYNTHETIC_002").id == "SYNTHETIC_002"


def test_invalid_and_provenance():
    with pytest.raises(ValueError, match="height_source"):
        load_metric_building(sample(), height_m=2)
    with pytest.raises(ValueError, match="height_m"):
        load_metric_building(sample(), height_m=-1, height_source="measured")
    with pytest.raises(ValueError, match="height_source"):
        load_metric_building(sample(), height_source="measured")


def test_wfs_request_constraints():
    url = build_wfs_url(key="test-key", typename="lt_c_example", bbox=(129.06,35.17,129.08,35.19))
    assert "srsName=EPSG%3A4326" in url
    assert "key=test-key" in url
    with pytest.raises(ValueError, match="wide-area"):
        build_wfs_url(key="test", typename="lt_c_example", bbox=(127,34,128,35))
    with pytest.raises(ValueError):
        build_wfs_url(key="test", typename="../../etc/passwd", bbox=(129,35,129.01,35.01))


def test_mapanything_command_requires_explicit_local_checkout(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "01.jpg").write_bytes(b"fake")
    checkout = tmp_path / "map-anything"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts/demo_images_only_inference.py").write_text("# upstream test stub")
    cmd = mapanything_command(checkout, images, tmp_path / "result.glb")
    assert "--apache" in cmd and "--save_glb" in cmd
    assert colmap_commands(images, tmp_path / "work")[0][0] == "colmap"


def test_cli_evidence_hash(tmp_path):
    out = tmp_path / "result"
    assert main(["build", "--geojson", str(EX), "--out", str(out)]) == 0
    assert (out / "footprint.dxf").exists()
    assert not (out / "mass.obj").exists()
    evidence = json.loads((out / "evidence.json").read_text())
    assert evidence["input_sha256"] == hashlib.sha256(EX.read_bytes()).hexdigest()
    assert evidence["quality"]["status"] == "UNVERIFIED"
