using ACadSharp.Entities;

namespace AllInCad.ACadSharpProbe;

/// <summary>
/// Extracts geometry and properties from ACadSharp entities in the
/// all-in-cad-headless-census/v1 shape, so that the ACadSharp lane can be
/// compared against the ezdxf lane without opening a CAD host.
/// </summary>
internal static class CensusMapper
{
    /// <summary>
    /// Maps ACadSharp CLR type names onto the DXF entity names that ezdxf
    /// reports, so the two lanes are comparable type-independently.
    /// </summary>
    public static string NormalizeTypeName(Entity entity)
    {
        var name = entity.GetType().Name.ToUpperInvariant();
        return name switch
        {
            "TEXTENTITY" => "TEXT",
            "MTEXTENTITY" => "MTEXT",
            "LWPOLYLINEENTITY" => "LWPOLYLINE",
            "POLYLINE2D" or "POLYLINE3D" => "POLYLINE",
            "LINEENTITY" => "LINE",
            "CIRCLEENTITY" => "CIRCLE",
            "ARCENTITY" => "ARC",
            "INSERTENTITY" => "INSERT",
            "DIMENSION" => "DIMENSION",
            _ => name.Replace("ENTITY", string.Empty)
        };
    }

    public static Dictionary<string, object?> ExtractGeometry(Entity entity)
    {
        var geometry = new Dictionary<string, object?>();

        switch (entity)
        {
            case MText mtext:
                geometry["insert"] = Point(mtext.InsertPoint.X, mtext.InsertPoint.Y, mtext.InsertPoint.Z);
                break;

            case TextEntity text:
                // MText derives from TextEntity, so the MText case above must
                // be matched first.
                geometry["insert"] = Point(text.InsertPoint.X, text.InsertPoint.Y, text.InsertPoint.Z);
                break;

            case Line line:
                geometry["start"] = Point(line.StartPoint.X, line.StartPoint.Y, line.StartPoint.Z);
                geometry["end"] = Point(line.EndPoint.X, line.EndPoint.Y, line.EndPoint.Z);
                break;

            case Arc arc:
                // ACadSharp exposes angles in radians; the ezdxf lane reports
                // degrees. Normalize here so both lanes digest identically.
                geometry["center"] = Point(arc.Center.X, arc.Center.Y, arc.Center.Z);
                geometry["radius"] = Round(arc.Radius);
                geometry["start_angle"] = Round(arc.StartAngle * 180.0 / Math.PI);
                geometry["end_angle"] = Round(arc.EndAngle * 180.0 / Math.PI);
                break;

            case Circle circle:
                geometry["center"] = Point(circle.Center.X, circle.Center.Y, circle.Center.Z);
                geometry["radius"] = Round(circle.Radius);
                break;

            case LwPolyline lw:
                geometry["points"] = lw.Vertices
                    .Select(vertex => new List<double>
                    {
                        Round(vertex.Location.X),
                        Round(vertex.Location.Y),
                        Round(vertex.StartWidth),
                        Round(vertex.EndWidth),
                        Round(vertex.Bulge)
                    })
                    .Cast<object>()
                    .ToList();
                geometry["closed"] = lw.IsClosed;
                break;

            case Insert insert:
                geometry["insert"] = Point(insert.InsertPoint.X, insert.InsertPoint.Y, insert.InsertPoint.Z);
                break;
        }

        return geometry;
    }

    public static Dictionary<string, object?> ExtractProperties(Entity entity)
    {
        var properties = new Dictionary<string, object?>();

        switch (entity)
        {
            case MText mtext:
                // ezdxf returns plain text with "\n"; the raw DXF escape is
                // "\P". Normalize to the ezdxf convention so both lanes agree.
                properties["text"] = mtext.Value?.Replace("\\P", "\n");
                break;

            case TextEntity text:
                properties["text"] = text.Value;
                break;

            case Insert insert:
                properties["block_name"] = insert.Block?.Name;
                break;
        }

        return properties;
    }

    // Emit coordinates as JSON doubles (e.g. 50.0 rather than 50) so that the
    // resulting census compares equal against the ezdxf lane, which always
    // serializes coordinates as floats. A whole-number coordinate otherwise
    // hashes differently across lanes for an identical drawing.
    private static double Round(double value) => Math.Round(value, 6);

    private static List<double> Point(double x, double y, double z = 0.0) =>
        new() { Round(x), Round(y), Round(z) };
}
