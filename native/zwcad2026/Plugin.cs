// ZWCAD 2026 host shell intentionally contains no guessed SDK namespaces.
// After the installed ZRX.NET SDK is detected on the target Windows machine,
// bind the real ZwSoft managed assemblies and implement AIC_STATUS plus the
// Named Pipe worker behind the shared All-In-Cad protocol.
namespace AllInCad.ZWCAD2026;

public static class BootstrapMarker
{
    public const string TargetHost = "ZWCAD 2026";
    public const string PreferredExecution = "PyRx/ZRX first, ZRX.NET second, LISP fallback last";
}
