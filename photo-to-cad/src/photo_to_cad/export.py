"""Headless exports. DXF is in millimetres and OBJ/IFC are in metres."""
from __future__ import annotations

from pathlib import Path

from .core import MetricBuilding


def write_dxf(building: MetricBuilding, path: str | Path) -> Path:
    import ezdxf
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2013", setup=True)
    doc.header["$INSUNITS"] = 4  # 4 = millimetres (ZWCAD/AutoCAD conventions)
    for layer, colour in (("GIS_FOOTPRINT", 7), ("GIS_HOLES", 6), ("MODEL_NOTES", 2)):
        doc.layers.new(layer, dxfattribs={"color": colour})
    msp = doc.modelspace()
    for p in building.polygons:
        msp.add_lwpolyline([(x * 1000, y * 1000) for x, y in p.exterior.coords[:-1]],
                           close=True, dxfattribs={"layer": "GIS_FOOTPRINT"})
        for hole in p.interiors:
            msp.add_lwpolyline([(x * 1000, y * 1000) for x, y in hole.coords[:-1]],
                               close=True, dxfattribs={"layer": "GIS_HOLES"})
    label = (f"GIS footprint, NOT SURVEY VERIFIED; source={building.source}; "
             f"UTM EPSG:{building.epsg}; area={building.footprint_area_m2:.2f}m2")
    msp.add_text(label, dxfattribs={"height": 220, "layer": "MODEL_NOTES", "insert": (0, -850)})
    if building.height_m is not None:
        msp.add_text(f"height {building.height_m:.3f}m [{building.height_source}]", dxfattribs={
            "height": 220, "layer": "MODEL_NOTES", "insert": (0, -1250)})
    doc.saveas(path)
    return path


def write_obj(building: MetricBuilding, path: str | Path) -> Path:
    if building.height_m is None:
        raise ValueError("3D extrusion requires an explicitly sourced height_m")
    from shapely.ops import triangulate
    from shapely.geometry import Polygon
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Non-surveyed building extrusion; height source: {building.height_source}"]
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []

    def vertex(x: float, y: float, z: float) -> int:
        vertices.append((x, y, z))
        return len(vertices)

    for poly in building.polygons:
        # side walls: exterior and any courtyards
        for ring in [poly.exterior, *poly.interiors]:
            points = list(ring.coords)[:-1]
            lows = [vertex(float(x), float(y), 0) for x, y in points]
            highs = [vertex(float(x), float(y), building.height_m) for x, y in points]
            for i in range(len(points)):
                j = (i + 1) % len(points)
                faces.append((lows[i], lows[j], highs[j], highs[i]))
        for tri in triangulate(poly):
            if not poly.covers(tri.representative_point()):
                continue
            coords = list(tri.exterior.coords)[:3]
            if not poly.covers(tri):
                # Avoid inventing roof area outside concave/holey input.
                continue
            top = [vertex(float(x), float(y), building.height_m) for x, y in coords]
            bottom = [vertex(float(x), float(y), 0) for x, y in coords]
            faces.append(tuple(top))
            faces.append(tuple(reversed(bottom)))
    lines.extend(f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices)
    lines.extend("f " + " ".join(map(str, f)) for f in faces)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_ifc(building: MetricBuilding, path: str | Path) -> Path:
    """Create a simple extruded IFC4 mass, not walls, windows or a permit-ready BIM."""
    if building.height_m is None:
        raise ValueError("IFC extrusion requires explicitly sourced height_m")
    if any(p.interiors for p in building.polygons):
        raise ValueError("IFC draft only supports polygons without holes; use OBJ for courtyards")
    try:
        import ifcopenshell
        from ifcopenshell.guid import new as guid_new
    except ImportError as exc:
        raise RuntimeError("IFC export requires ifcopenshell optional dependency: pip install 'photo-to-cad[ifc]'") from exc
    f = ifcopenshell.file(schema="IFC4")
    g = guid_new
    project = f.createIfcProject(GlobalId=g(), Name="Photo-to-CAD metric reference model")
    units = [f.createIfcSIUnit(UnitType=t, Name=n) for t, n in
             (("LENGTHUNIT", "METRE"), ("AREAUNIT", "SQUARE_METRE"), ("VOLUMEUNIT", "CUBIC_METRE"))]
    project.UnitsInContext = f.createIfcUnitAssignment(units)
    point3 = lambda x,y,z: f.createIfcCartesianPoint((float(x),float(y),float(z)))
    place3 = lambda: f.createIfcAxis2Placement3D(point3(0,0,0))
    context = f.createIfcGeometricRepresentationContext(ContextIdentifier="Model", ContextType="Model",
                                CoordinateSpaceDimension=3, Precision=1e-5, WorldCoordinateSystem=place3())
    project.RepresentationContexts = [context]
    site = f.createIfcSite(GlobalId=g(), Name="GIS derived site", CompositionType="ELEMENT")
    structure = f.createIfcBuilding(GlobalId=g(), Name=building.id, CompositionType="ELEMENT")
    storey = f.createIfcBuildingStorey(GlobalId=g(), Name="Reference mass", CompositionType="ELEMENT")
    for parent, child in ((project, site), (site, structure), (structure, storey)):
        f.createIfcRelAggregates(GlobalId=g(), RelatingObject=parent, RelatedObjects=[child])
    elements = []
    for i,p in enumerate(building.polygons):
        xy = [f.createIfcCartesianPoint((float(x), float(y))) for x,y in p.exterior.coords]
        loop = f.createIfcPolyline(xy)
        profile = f.createIfcArbitraryClosedProfileDef("AREA", None, loop)
        solid = f.createIfcExtrudedAreaSolid(profile, place3(), f.createIfcDirection((0.,0.,1.)), float(building.height_m))
        shape = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])
        representation = f.createIfcProductDefinitionShape(None, None, [shape])
        element = f.createIfcBuildingElementProxy(GlobalId=g(), Name=f"Unverified GIS building mass {i+1}",
                            ObjectType=f"GIS footprint | height:{building.height_source}",
                            ObjectPlacement=f.createIfcLocalPlacement(None, place3()), Representation=representation)
        elements.append(element)
    f.createIfcRelContainedInSpatialStructure(GlobalId=g(), RelatingStructure=storey, RelatedElements=elements)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f.write(str(path))
    return path
