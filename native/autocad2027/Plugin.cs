#if AIC_AUTOCAD_SDK
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;

namespace AllInCad.AutoCAD2027;

public sealed class Plugin : IExtensionApplication
{
    public void Initialize()
    {
        WriteMessage("\nAll-In-Cad AutoCAD 2027 adapter loaded. Native pipe worker not enabled yet.");
    }

    public void Terminate() { }

    [CommandMethod("AIC_STATUS", CommandFlags.Session)]
    public static void Status()
    {
        var doc = Application.DocumentManager.MdiActiveDocument;
        if (doc is null)
        {
            WriteMessage("\nAIC: no active document.");
            return;
        }
        WriteMessage($"\nAIC: AutoCAD 2027 host ready; document={doc.Name}");
    }

    private static void WriteMessage(string message)
    {
        Editor? editor = Application.DocumentManager.MdiActiveDocument?.Editor;
        editor?.WriteMessage(message);
    }
}
#endif
