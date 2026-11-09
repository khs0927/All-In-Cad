using System.Text.Json;
using AllInCad.NativeProtocol;

// Headless checks for HostWriteGuard / SendCommandPolicy. Mirrors tests/test_host_approval.py and
// replays the shared cross-language vectors so the C# gate and the Python reference cannot drift.
int failures = 0;
int passed = 0;

void Check(string name, bool condition, string detail = "")
{
    if (condition)
    {
        passed++;
        return;
    }

    failures++;
    Console.Error.WriteLine("FAIL " + name + (detail.Length > 0 ? ": " + detail : string.Empty));
}

string? Refusal(Action action)
{
    try
    {
        action();
        return null;
    }
    catch (HostApprovalException ex)
    {
        return ex.Code;
    }
}

JsonElement Json(string text)
{
    using JsonDocument doc = JsonDocument.Parse(text);
    return doc.RootElement.Clone();
}

JsonElement WithToken(string paramsJson, string token)
{
    var dict = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(paramsJson)!;
    dict[HostWriteGuard.TokenParam] = JsonSerializer.SerializeToElement(token);
    return JsonSerializer.SerializeToElement(dict);
}

string vectorsPath = args.Length > 0
    ? args[0]
    : Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "tests", "fixtures", "host_approval_vectors.json");
using (JsonDocument vectors = JsonDocument.Parse(File.ReadAllText(vectorsPath)))
{
    foreach (JsonElement row in vectors.RootElement.GetProperty("digests").EnumerateArray())
    {
        string method = row.GetProperty("method").GetString()!;
        JsonElement p = Json(row.GetProperty("params_json").GetString()!);
        string expected = row.GetProperty("digest").GetString()!;
        string actual = HostWriteGuard.OperationDigest(method, p);
        Check("vector digest " + method + " " + row.GetProperty("params_json").GetString(), actual == expected, actual + " != " + expected);
        bool requires = row.GetProperty("requires_approval").GetBoolean();
        Check("vector requires_approval " + method, HostWriteGuard.RequiresApproval(method, p) == requires);
    }

    foreach (JsonElement row in vectors.RootElement.GetProperty("numbers").EnumerateArray())
    {
        string text = row.GetProperty("json").GetString()!;
        string actual = CanonicalJson.Serialize(Json(text));
        Check("vector number " + text, actual == row.GetProperty("canonical").GetString(), actual);
    }

    var policy = new SendCommandPolicy();
    foreach (JsonElement row in vectors.RootElement.GetProperty("policy").EnumerateArray())
    {
        string method = row.GetProperty("method").GetString()!;
        JsonElement p = Json(row.GetProperty("params_json").GetString()!);
        string? expected = row.GetProperty("error_code").ValueKind == JsonValueKind.Null ? null : row.GetProperty("error_code").GetString();
        string? actual = Refusal(() => policy.Check(method, p));
        Check("vector policy " + row.GetProperty("params_json").GetString(), actual == expected, (actual ?? "allowed") + " != " + (expected ?? "allowed"));
    }
}

const string LineParams = """{"x1": 0.0, "y1": 0.0, "x2": 1234.5, "y2": 678.9}""";
long now = 1_000_000;
var guard = new HostWriteGuard(clock: () => now);

// Rejected without a token.
Check("no token", Refusal(() => guard.Authorize("host.draw_line", Json(LineParams))) == "E_APPROVAL_REQUIRED");

// Read-only methods are never gated.
Check("read-only passes", Refusal(() => guard.Authorize("host.context", Json("{}"))) is null);

// Valid token accepted once, then reused -> rejected.
var plan = guard.Plan("host.draw_line", Json(LineParams));
string token = (string)plan["approval_token"]!;
Check("plan digest", (string)plan["operation_digest"]! == HostWriteGuard.OperationDigest("host.draw_line", Json(LineParams)));
Check("valid token", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, token))) is null);
Check("reused token", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, token))) == "E_APPROVAL_REUSED");

// Number spelling does not matter (PowerShell may send 0 or 0.0), the value does.
token = (string)guard.Plan("host.draw_line", Json(LineParams))["approval_token"]!;
Check("integral spelling", Refusal(() => guard.Authorize("host.draw_line", WithToken("""{"x1": 0, "y1": 0, "x2": 1234.5, "y2": 678.9}""", token))) is null);

// Wrong params / wrong method -> mismatch, and the token stays usable for the planned op.
token = (string)guard.Plan("host.draw_line", Json(LineParams))["approval_token"]!;
Check("wrong params", Refusal(() => guard.Authorize("host.draw_line", WithToken("""{"x1": 0.0, "y1": 0.0, "x2": 9999, "y2": 678.9}""", token))) == "E_APPROVAL_MISMATCH");
Check("wrong method", Refusal(() => guard.Authorize("xicad.rollback", WithToken(LineParams, token))) == "E_APPROVAL_MISMATCH");
Check("planned op after mismatch", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, token))) is null);

// Forged / garbage tokens.
Check("garbage token", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, "not-a-token"))) == "E_APPROVAL_INVALID");
var other = new HostWriteGuard(clock: () => now);
string foreign = (string)other.Plan("host.draw_line", Json(LineParams))["approval_token"]!;
Check("foreign process token", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, foreign))) == "E_APPROVAL_INVALID");
token = (string)guard.Plan("host.draw_line", Json(LineParams))["approval_token"]!;
string tampered = token[..(token.IndexOf('.') + 1)] + (token[token.IndexOf('.') + 1] == 'A' ? "B" : "A") + token[(token.IndexOf('.') + 2)..];
Check("tampered signature", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, tampered))) == "E_APPROVAL_INVALID");
Check("non-string token", Refusal(() => guard.Authorize("host.draw_line", Json("""{"x1": 0, "approval_token": 5}"""))) == "E_APPROVAL_INVALID");

// Expired.
token = (string)guard.Plan("host.draw_line", Json(LineParams))["approval_token"]!;
now += HostWriteGuard.DefaultTtlSeconds + 1;
Check("expired token", Refusal(() => guard.Authorize("host.draw_line", WithToken(LineParams, token))) == "E_APPROVAL_EXPIRED");

// Planning validates: denied commands never get a token; read-only methods cannot be planned.
Check("plan denied command", Refusal(() => guard.Plan("host.send_command", Json("""{"command": "SHELL"}"""))) == "E_COMMAND_DENIED");
Check("plan read-only", Refusal(() => guard.Plan("host.context", Json("{}"))) == "E_BAD_PARAMS");
Check("plan with token", Refusal(() => guard.Plan("host.draw_line", Json("""{"approval_token": "x"}"""))) == "E_BAD_PARAMS");
Check("conditional probe", Refusal(() => guard.Authorize("xicad.probe", Json("""{"force_load": true}"""))) == "E_APPROVAL_REQUIRED");
Check("plain probe", Refusal(() => guard.Authorize("xicad.probe", Json("{}"))) is null);

// Opt-out: AIC_REQUIRE_APPROVAL=0 skips the token, never the command denylist.
var env = new Dictionary<string, string?> { [HostWriteGuard.EnvRequire] = "0" };
var open = HostWriteGuard.FromEnvironment(name => env.GetValueOrDefault(name));
Check("opt-out flag", !open.Required);
Check("opt-out draws", Refusal(() => open.Authorize("host.draw_line", Json(LineParams))) is null);
Check("opt-out keeps denylist", Refusal(() => open.Authorize("host.send_command", Json("""{"command": "QUIT Y"}"""))) == "E_COMMAND_DENIED");
Check("default is on", HostWriteGuard.FromEnvironment(_ => null).Required);
Check("unknown value stays on", HostWriteGuard.FromEnvironment(name => name == HostWriteGuard.EnvRequire ? "maybe" : null).Required);

// Allowlist / deny extension / LISP opt-in.
env = new Dictionary<string, string?>
{
    [SendCommandPolicy.EnvAllow] = "LINE, ZOOM",
    [SendCommandPolicy.EnvDeny] = "ERASE",
    [SendCommandPolicy.EnvAllowLisp] = "1",
};
var custom = SendCommandPolicy.FromEnvironment(name => env.GetValueOrDefault(name));
Check("allowlisted", Refusal(() => custom.CheckText("_.LINE 0,0 1,1 ")) is null);
Check("not allowlisted", Refusal(() => custom.CheckText("CIRCLE 0,0 5")) == "E_COMMAND_DENIED");
Check("extra deny", Refusal(() => custom.CheckText("ZOOM E ERASE L ")) == "E_COMMAND_DENIED");
Check("lisp opt-in", Refusal(() => custom.CheckText("LINE (getvar \"LASTPOINT\")")) is null);

// TTL from environment is clamped.
Check("ttl clamp", HostWriteGuard.FromEnvironment(name => name == HostWriteGuard.EnvTtl ? "99999" : null).TtlSeconds == HostWriteGuard.MaxTtlSeconds);

Console.WriteLine("host-write-guard checks: passed=" + passed + " failed=" + failures);
return failures == 0 ? 0 : 1;
