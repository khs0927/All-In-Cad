# Photo-to-CAD / Road-view-to-CAD MVP

**Evidence-first**, free-first building reconstruction experiment for iPhone users and architectural CAD automation.
A synthetic footprint can be transformed to *real metric coordinates* and exported to DXF (millimetres); an **explicit** height can generate mass OBJ and optional IFC4. **No output should be considered survey-grade without independent ground checks.**

## Status (2026-11-13)

| Capability | Status |
|--|--|
| GeoJSON WGS84 footprint -> local UTM metric model | implemented, tested offline |
| Source SHA256, provenance, uncertainty/status report | implemented, tested offline |
| Headless DXF R2013 (mm), reopened with ezdxf | implemented, tested offline |
| Height-source-gated OBJ volumetric mass | implemented, tested offline |
| Optional IFC4 proxy mass via IfcOpenShell | implemented, **runtime unavailable in offline test environment** |
| VWorld WFS request adapter | implemented, offline URL tests only; **API key/layer/live result not yet verified** |
| MapAnything Apache GPU adapter | command integration only; **weights/GPU/inference NOT run** |
| COLMAP SfM adapter | commands prepared; **photogrammetry NOT run** |
| Cloudflare Worker gateway | source present, NOT deployed; Oracle backend NOT connected |
| Automatic walls, windows, roof topology, image-to-CAD alignment | NOT implemented |

## Run from clean Python environment

```bash
cd photo-to-cad
python -m pip install -e '.[test]'
pytest -q
photo-to-cad build --geojson examples/footprint.geojson --out ./outputs/demo
photo-to-cad build --geojson examples/footprint.geojson --height-m 9.5 --height-source estimated --out ./outputs/demo3d
# Optional, only where IfcOpenShell wheels are available:
python -m pip install -e '.[ifc]'
photo-to-cad build --geojson examples/footprint.geojson --height-m 9.5 --height-source measured --ifc --out ./outputs/demoifc
```

Outputs: `footprint.dxf`, optionally `mass.obj` and `mass.ifc`, plus `evidence.json` with projected CRS, local-origin offset, input SHA256, output SHA256 and explicit UNVERIFIED status.

**Do not treat axes of the minimum rotated bounding rectangle as measured building width/length.** They are reference estimates derived from source polygon geometry.

## VWorld optional live acquisition

Request a free VWorld developer API key separately. Do not commit it; set `VWORLD_API_KEY` only in the invoking environment. From the official layer catalog choose a **verified type name** for building geometry; do not assume a generic building layer name. Use a narrowly bounded WGS84 region, then select one feature by ID; multi-feature exports never silently choose a building.

```bash
export VWORLD_API_KEY='your-own-key'
photo-to-cad vworld --typename YOUR_VERIFIED_BUILDING_LAYER --bbox '129.07,35.18,129.071,35.181' --out ./outputs/buildings.geojson
photo-to-cad build --geojson ./outputs/buildings.geojson --feature-id AN_ACTUAL_FEATURE_ID --out ./outputs/building
```

VWorld service availability, domain referrer checks, valid API type names, response CRS and authorization must be checked on a **live** request. Dataset may describe a cadastral/roof polygon, not surveyed actual wall faces.

## AI reconstruction adapter (research lanes, GPU not included)

- Official MapAnything: https://github.com/facebookresearch/map-anything . Use **Apache model** (`--apache`) for commercial-friendly weights. Run only on a GPU machine with sufficient resources, never on Cloudflare Workers or the existing 1GB Oracle n8n VM.
- COLMAP: https://github.com/colmap/colmap . The included `colmap_commands()` assembles **relative-scale** SfM commands. They do not make a reconstruction metric by themselves.
- Alignment of model output to GIS geometry and independent error statistics is **not yet implemented**. Do not pretend MapAnything output and the GIS footprint are already fused.

## Cloudflare + Oracle deployment plan (NOT executed)

Cloudflare Worker (`worker/index.mjs`) can authenticate mobile job submissions and forward only manifest references, not arbitrary raw URLs/photos or command text. Requires three bound values: `API_BEARER_TOKEN`, `JOB_BACKEND_TOKEN`, `JOB_BACKEND_URL`. Backend must be a separate **HTTPS** job service and must independently authorize the manifest; none is deployed by this PR. Existing Oracle n8n VM is to remain untouched. Dedicated future worker CPU ≠ GPU; GPU execution depends on authorized external/free compute availability. No runtime costs or API keys are auto-created.

## Security / CAD verification

Never auto-modify a live DWG. Source footprints can differ from true exterior wall outlines. All exports are new files. The host application must independently read back entities and elevations; any final native CAD write needs the existing All-In-Cad approval, revision, and idempotency controls. Street-view imagery must have a lawful reconstruction license.

## Next milestone to actually reconstruct one building

1. Acquire 3+ licensed images with separate camera centers and a verified building footprint.
2. Run MapAnything Apache / COLMAP on authorized GPU, save point cloud/camera poses and SHA256 evidence.
3. Implement robust scale/rigid alignment, report withheld check distances and uncertainties.
4. Extract walls/openings, hand-check, export IFC wall/window semantics and CAD layers.
5. Deploy worker + backend only once permissions/quotas/authorizations have been reviewed.
