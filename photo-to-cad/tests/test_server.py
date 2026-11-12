import json
import threading
from http.client import HTTPConnection
from pathlib import Path
import pytest

from photo_to_cad.server import JobServer, run_job

EX = Path(__file__).resolve().parents[1] / "examples" / "footprint.geojson"


def make_fixture(tmp_path):
    p = tmp_path / "inbox" / "project"
    p.mkdir(parents=True)
    (p / "building.geojson").write_bytes(EX.read_bytes())
    (p / "manifest.json").write_text(json.dumps({"geojson":"building.geojson","height_m":5.0,
                                                 "height_source":"registered"}))
    return "project/manifest.json"


def test_job_idempotent_and_private(tmp_path):
    ref = make_fixture(tmp_path)
    a = run_job(data_root=tmp_path, job_id="safe_0001", manifest_ref=ref)
    b = run_job(data_root=tmp_path, job_id="safe_0001", manifest_ref=ref)
    assert a == b
    assert (tmp_path / "jobs/safe_0001/footprint.dxf").exists()
    assert (tmp_path / "jobs/safe_0001/mass.obj").exists()
    with pytest.raises(ValueError, match="Invalid relative|outside"):
        run_job(data_root=tmp_path, job_id="safe_0002", manifest_ref="../../secret.json")
    with pytest.raises(ValueError, match="collision"):
        run_job(data_root=tmp_path, job_id="safe_0001", manifest_ref="other/manifest.json")


def test_server_auth_and_http(tmp_path):
    ref = make_fixture(tmp_path)
    server=JobServer(("127.0.0.1",0),data_root=tmp_path, token="S"*24)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        c=HTTPConnection("127.0.0.1",server.server_port,timeout=3)
        c.request("GET","/health");resp=c.getresponse();assert resp.status==200;resp.read()
        body=json.dumps({"job_id":"safe_0002","input_manifest_ref":ref})
        c.request("POST","/jobs",body,headers={"content-type":"application/json"})
        resp=c.getresponse();assert resp.status==401;resp.read()
        c.request("POST","/jobs",body,headers={"authorization":"Bearer "+"S"*24,
                                          "content-type":"application/json"})
        resp=c.getresponse();assert resp.status==201
        result=json.loads(resp.read())
        assert result["accepted"] is True
        assert result["result"]["quality"]["status"]=="UNVERIFIED"
        c.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
