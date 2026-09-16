using System.Text.Json;
using ACadSharp;
using ACadSharp.IO;

if (args.Length == 1 && args[0] == "--capabilities")
{
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        adapter = "acadsharp",
        assembly = typeof(CadDocument).Assembly.FullName,
        reader = typeof(DwgReader).FullName,
        output = "all-in-cad-headless-census/v1"
    }));
    return 0;
}

if (args.Length != 1)
{
    Console.Error.WriteLine("usage: AllInCad.ACadSharpProbe <drawing.dwg> | --capabilities");
    return 2;
}

try
{
    var source = Path.GetFullPath(args[0]);
    var doc = DwgReader.Read(source);
    var entities = doc.Entities.Select(entity => new
    {
        handle = entity.Handle.ToString("X"),
        entity_type = entity.GetType().Name.ToUpperInvariant(),
        layer = entity.Layer?.Name ?? "0",
        geometry = new Dictionary<string, object?>(),
        properties = new Dictionary<string, object?>()
    }).ToArray();

    Console.WriteLine(JsonSerializer.Serialize(new
    {
        schema = "all-in-cad-headless-census/v1",
        source,
        entity_count = entities.Length,
        entities
    }));
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex.ToString());
    return 1;
}
