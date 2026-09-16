# ZWCAD 2026 native adapter

Preferred authoring host. Bring-up order:

1. Test PyRx ZRX loader in ZWCAD 2026 and record the common capability matrix.
2. Use `IFox.CAD.ZCAD2025` / Fs.Fox.CAD helpers where they reduce ZRX.NET boilerplate.
3. Compile a small ZRX.NET host plugin only for capabilities that need a dedicated local worker.
4. Keep `dalingo81/ZWCAD-MCP` LISP/File-IPC as a declared fallback for unsupported operations.

Do not assume AutoCAD and ZWCAD managed assemblies are binary interchangeable. Shared business logic must stay SDK-neutral; host projects bind their own vendor assemblies.
