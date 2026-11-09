using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace AllInCad.NativeProtocol;

/// <summary>
/// Approval gate for every pipe method that writes to the drawing or executes command-line /
/// LISP text. Contract (identical to the Python reference <c>all_in_cad.host_approval</c>, both
/// checked against <c>tests/fixtures/host_approval_vectors.json</c>):
/// <list type="number">
/// <item><c>host.plan_write {method, params}</c> validates the operation, stores a pending plan
/// and returns <c>plan_id</c>, <c>approval_token</c>, <c>operation_digest</c>, <c>expires_at</c>.
/// Nothing is written.</item>
/// <item>The gated method is then called with the same params plus <c>params.approval_token</c>.
/// The token must be HMAC-signed by this process, bound to the exact method and canonical params
/// digest, unexpired and unused. It is consumed on use.</item>
/// </list>
/// ON by default; <c>AIC_REQUIRE_APPROVAL=0</c> is the documented opt-out for trusted local
/// automation. The send_command policy (denylist, optional allowlist, LISP refusal) is always
/// enforced, opt-out or not.
/// </summary>
public sealed class HostWriteGuard
{
    public const string PlanMethod = "host.plan_write";
    public const string TokenParam = "approval_token";
    public const string EnvRequire = "AIC_REQUIRE_APPROVAL";
    public const string EnvTtl = "AIC_APPROVAL_TTL_SECONDS";
    public const int DefaultTtlSeconds = 120;
    public const int MaxTtlSeconds = 600;
    public const int MaxPending = 64;

    public static readonly IReadOnlyCollection<string> GatedMethods = new SortedSet<string>(StringComparer.Ordinal)
    {
        "host.send_command",
        "host.draw_line",
        "xicad.ensure_loaded",
        "xicad.draw_wall",
        "xicad.draw_door",
        "xicad.draw_window",
        "xicad.smoke_wall_door_window",
        "xicad.run",
        "xicad.rollback",
    };

    /// <summary>Gated only for some params: xicad.probe loads a LISP file when force_load is true.</summary>
    public static readonly IReadOnlyCollection<string> ConditionalMethods = new SortedSet<string>(StringComparer.Ordinal)
    {
        "xicad.probe",
    };

    private readonly byte[] secret;
    private readonly object gate = new();
    private readonly Dictionary<string, Pending> pending = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> consumed = new(StringComparer.Ordinal);
    private readonly Func<long> clock;

    public HostWriteGuard(
        bool required = true,
        int ttlSeconds = DefaultTtlSeconds,
        SendCommandPolicy? policy = null,
        byte[]? secret = null,
        Func<long>? clock = null)
    {
        if (ttlSeconds < 1 || ttlSeconds > MaxTtlSeconds)
        {
            throw new ArgumentOutOfRangeException(nameof(ttlSeconds));
        }

        this.secret = secret ?? RandomNumberGenerator.GetBytes(32);
        if (this.secret.Length < 32)
        {
            throw new ArgumentException("approval secret must be at least 32 bytes", nameof(secret));
        }

        Required = required;
        TtlSeconds = ttlSeconds;
        Policy = policy ?? new SendCommandPolicy();
        this.clock = clock ?? (() => DateTimeOffset.UtcNow.ToUnixTimeSeconds());
    }

    public bool Required { get; }

    public int TtlSeconds { get; }

    public SendCommandPolicy Policy { get; }

    public static HostWriteGuard FromEnvironment(Func<string, string?>? read = null)
    {
        read ??= Environment.GetEnvironmentVariable;
        string? require = read(EnvRequire)?.Trim().ToLowerInvariant();
        bool off = require is "0" or "false" or "off" or "no";
        int ttl = DefaultTtlSeconds;
        if (int.TryParse(read(EnvTtl), NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed))
        {
            ttl = Math.Clamp(parsed, 1, MaxTtlSeconds);
        }

        return new HostWriteGuard(!off, ttl, SendCommandPolicy.FromEnvironment(read));
    }

    public static bool RequiresApproval(string method, JsonElement parameters)
    {
        if (GatedMethods.Contains(method))
        {
            return true;
        }

        // Fail closed: anything but an absent / null / false force_load counts as a load.
        return ConditionalMethods.Contains(method)
            && parameters.ValueKind == JsonValueKind.Object
            && parameters.TryGetProperty("force_load", out JsonElement force)
            && force.ValueKind is not (JsonValueKind.Null or JsonValueKind.False);
    }

    public static string OperationDigest(string method, JsonElement parameters)
    {
        var builder = new StringBuilder();
        builder.Append("{\"method\":");
        CanonicalJson.WriteString(builder, method);
        builder.Append(",\"params\":");
        CanonicalJson.WriteObject(builder, parameters, skipKey: TokenParam);
        builder.Append('}');
        return Convert.ToHexString(SHA256.HashData(Encoding.ASCII.GetBytes(builder.ToString()))).ToLowerInvariant();
    }

    public Dictionary<string, object?> Describe() => new()
    {
        ["required"] = Required,
        ["plan_method"] = PlanMethod,
        ["token_param"] = TokenParam,
        ["ttl_seconds"] = TtlSeconds,
        ["gated_methods"] = GatedMethods.ToArray(),
        ["conditional_methods"] = ConditionalMethods.ToArray(),
        ["send_command"] = new Dictionary<string, object?>
        {
            ["denylist_size"] = Policy.Denylist.Count,
            ["allowlist_active"] = Policy.Allowlist.Count > 0,
            ["lisp_allowed"] = Policy.AllowLisp,
        },
    };

    /// <summary>host.plan_write: validate the operation and issue a single-use token.</summary>
    public Dictionary<string, object?> Plan(string? method, JsonElement parameters)
    {
        if (string.IsNullOrWhiteSpace(method) || !RequiresApproval(method, parameters))
        {
            throw new HostApprovalException("E_BAD_PARAMS", "method '" + method + "' with these params does not need approval");
        }

        if (parameters.ValueKind is not (JsonValueKind.Object or JsonValueKind.Undefined or JsonValueKind.Null))
        {
            throw new HostApprovalException("E_BAD_PARAMS", "params must be an object");
        }

        if (parameters.ValueKind == JsonValueKind.Object && parameters.TryGetProperty(TokenParam, out _))
        {
            throw new HostApprovalException("E_BAD_PARAMS", "params must not contain " + TokenParam);
        }

        Policy.Check(method, parameters);
        long issued = clock();
        string digest = OperationDigest(method, parameters);
        string planId = Guid.NewGuid().ToString("N");
        long expiresAt = issued + TtlSeconds;
        var body = new StringBuilder();
        body.Append("{\"digest\":");
        CanonicalJson.WriteString(body, digest);
        body.Append(",\"exp\":").Append(expiresAt.ToString(CultureInfo.InvariantCulture));
        body.Append(",\"method\":");
        CanonicalJson.WriteString(body, method);
        body.Append(",\"nonce\":");
        CanonicalJson.WriteString(body, Convert.ToHexString(RandomNumberGenerator.GetBytes(8)).ToLowerInvariant());
        body.Append(",\"plan_id\":");
        CanonicalJson.WriteString(body, planId);
        body.Append(",\"v\":1}");
        byte[] bodyBytes = Encoding.ASCII.GetBytes(body.ToString());
        string token = B64Url(bodyBytes) + "." + B64Url(Sign(bodyBytes));

        lock (gate)
        {
            Prune(issued);
            if (pending.Count >= MaxPending)
            {
                throw new HostApprovalException("E_APPROVAL_BUSY", "too many pending plans; retry later");
            }

            pending[planId] = new Pending(method, digest, expiresAt);
        }

        object? operationParams = parameters.ValueKind == JsonValueKind.Object
            ? JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(parameters.GetRawText())
            : new Dictionary<string, JsonElement>();
        return new Dictionary<string, object?>
        {
            ["plan_id"] = planId,
            ["approval_token"] = token,
            ["method"] = method,
            ["operation_digest"] = digest,
            ["operation"] = new Dictionary<string, object?> { ["method"] = method, ["params"] = operationParams },
            ["expires_at"] = expiresAt,
            ["ttl_seconds"] = TtlSeconds,
            ["approval_required"] = Required,
            ["note"] = "Nothing was written. Re-send " + method + " with these params plus params."
                + TokenParam + " before expires_at; the token works once.",
        };
    }

    /// <summary>
    /// Gate a call before it is dispatched. Returns the consumed plan id, or null when nothing was
    /// needed. Throws <see cref="HostApprovalException"/> on refusal.
    /// </summary>
    public string? Authorize(string method, JsonElement parameters)
    {
        if (!RequiresApproval(method, parameters))
        {
            return null;
        }

        Policy.Check(method, parameters);
        if (!Required)
        {
            return null;
        }

        string? token = null;
        if (parameters.ValueKind == JsonValueKind.Object && parameters.TryGetProperty(TokenParam, out JsonElement tokenElement))
        {
            if (tokenElement.ValueKind != JsonValueKind.String && tokenElement.ValueKind != JsonValueKind.Null)
            {
                throw new HostApprovalException("E_APPROVAL_INVALID", "approval_token must be a string");
            }

            token = tokenElement.ValueKind == JsonValueKind.String ? tokenElement.GetString() : null;
        }

        if (string.IsNullOrEmpty(token))
        {
            throw new HostApprovalException(
                "E_APPROVAL_REQUIRED",
                method + " needs an approval token: call " + PlanMethod + " first (opt out with "
                + EnvRequire + "=0 for trusted local automation)");
        }

        byte[] body;
        byte[] signature;
        string? tokenMethod;
        string? tokenDigest;
        string? planId;
        long exp;
        try
        {
            int dot = token.IndexOf('.');
            if (dot <= 0)
            {
                throw new FormatException("no separator");
            }

            body = B64UrlDecode(token[..dot]);
            signature = B64UrlDecode(token[(dot + 1)..]);
            using JsonDocument payload = JsonDocument.Parse(body);
            JsonElement root = payload.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                throw new FormatException("payload is not an object");
            }

            tokenMethod = root.TryGetProperty("method", out JsonElement m) && m.ValueKind == JsonValueKind.String ? m.GetString() : null;
            tokenDigest = root.TryGetProperty("digest", out JsonElement d) && d.ValueKind == JsonValueKind.String ? d.GetString() : null;
            planId = root.TryGetProperty("plan_id", out JsonElement p) && p.ValueKind == JsonValueKind.String ? p.GetString() : null;
            exp = root.TryGetProperty("exp", out JsonElement e) && e.TryGetInt64(out long value) ? value : 0;
        }
        catch (Exception ex) when (ex is FormatException or JsonException or ArgumentException)
        {
            throw new HostApprovalException("E_APPROVAL_INVALID", "malformed approval token");
        }

        if (!CryptographicOperations.FixedTimeEquals(signature, Sign(body)))
        {
            throw new HostApprovalException("E_APPROVAL_INVALID", "approval token signature is invalid");
        }

        if (!string.Equals(tokenMethod, method, StringComparison.Ordinal)
            || !string.Equals(tokenDigest, OperationDigest(method, parameters), StringComparison.Ordinal))
        {
            throw new HostApprovalException("E_APPROVAL_MISMATCH", "approval token was issued for a different method or params");
        }

        long now = clock();
        lock (gate)
        {
            if (planId is null)
            {
                throw new HostApprovalException("E_APPROVAL_INVALID", "approval token has no plan_id");
            }

            if (consumed.ContainsKey(planId))
            {
                throw new HostApprovalException("E_APPROVAL_REUSED", "approval token was already used");
            }

            if (exp < now)
            {
                pending.Remove(planId);
                throw new HostApprovalException("E_APPROVAL_EXPIRED", "approval token expired; plan again");
            }

            if (!pending.Remove(planId, out Pending? entry) || !string.Equals(entry.Digest, tokenDigest, StringComparison.Ordinal))
            {
                throw new HostApprovalException("E_APPROVAL_INVALID", "approval token is not pending");
            }

            consumed[planId] = entry.ExpiresAt;
            Prune(now);
            return planId;
        }
    }

    private void Prune(long now)
    {
        foreach (string key in pending.Where(kv => kv.Value.ExpiresAt < now).Select(kv => kv.Key).ToList())
        {
            pending.Remove(key);
        }

        foreach (string key in consumed.Where(kv => kv.Value < now).Select(kv => kv.Key).ToList())
        {
            consumed.Remove(key);
        }
    }

    private byte[] Sign(byte[] body) => HMACSHA256.HashData(secret, body);

    private static string B64Url(byte[] data) =>
        Convert.ToBase64String(data).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static byte[] B64UrlDecode(string value)
    {
        string padded = value.Replace('-', '+').Replace('_', '/');
        padded += new string('=', (4 - padded.Length % 4) % 4);
        return Convert.FromBase64String(padded);
    }

    private sealed record Pending(string Method, string Digest, long ExpiresAt);
}

public sealed class HostApprovalException : Exception
{
    public HostApprovalException(string code, string message)
        : base(message)
    {
        Code = code;
    }

    public string Code { get; }
}

/// <summary>
/// Command-line text policy for host.send_command and xicad.run. Every whitespace-separated token
/// is checked against the denylist (space and newline are Enter on the AutoCAD command line, so any
/// later token can start a new command). LISP (any "(") is refused unless AIC_SEND_COMMAND_ALLOW_LISP=1.
/// When AIC_SEND_COMMAND_ALLOW is set, the first command must be on it.
/// </summary>
public sealed class SendCommandPolicy
{
    public const string EnvDeny = "AIC_SEND_COMMAND_DENY";
    public const string EnvAllow = "AIC_SEND_COMMAND_ALLOW";
    public const string EnvAllowLisp = "AIC_SEND_COMMAND_ALLOW_LISP";
    private const string PrefixChars = "._-'+";

    public static readonly IReadOnlyCollection<string> DefaultDenylist = new SortedSet<string>(StringComparer.Ordinal)
    {
        // process and code execution
        "SHELL", "SH", "START", "SCRIPT", "SCRIPTCALL", "RSCRIPT", "ACTPLAY",
        "VBARUN", "VBALOAD", "VBAIDE", "VBAMAN", "VBANEW", "VBASTMT", "VBAUNLOAD",
        "NETLOAD", "APPLOAD", "ARX", "LOADJSP", "WEBLOAD", "PYLOAD", "RUNPY",
        // menus / customisation that can carry macros
        "CUI", "CUILOAD", "CUIUNLOAD", "MENU", "MENULOAD", "MENUUNLOAD",
        // file writes and document lifecycle
        "SAVE", "QSAVE", "SAVEAS", "SAVEALL", "WBLOCK", "EXPORT", "DXFOUT", "EXPORTPDF",
        "PUBLISH", "PLOT", "ETRANSMIT", "ARCHIVE", "OPEN", "NEW", "QNEW", "RECOVER",
        "RECOVERALL", "CLOSE", "CLOSEALL", "QUIT", "EXIT", "PURGE",
        // security settings
        "SECURELOAD", "TRUSTEDPATHS", "TRUSTEDDOMAINS", "LEGACYCODESEARCH", "OPTIONS",
        // undo (C-2 hazard: a queued U can silently undo the user's own work)
        "U", "UNDO",
    };

    public SendCommandPolicy(
        IEnumerable<string>? extraDeny = null,
        IEnumerable<string>? allowlist = null,
        bool allowLisp = false)
    {
        var deny = new HashSet<string>(DefaultDenylist, StringComparer.Ordinal);
        foreach (string name in extraDeny ?? Array.Empty<string>())
        {
            deny.Add(Normalize(name));
        }

        deny.Remove(string.Empty);
        Denylist = deny;
        var allow = new HashSet<string>(StringComparer.Ordinal);
        foreach (string name in allowlist ?? Array.Empty<string>())
        {
            allow.Add(Normalize(name));
        }

        allow.Remove(string.Empty);
        Allowlist = allow;
        AllowLisp = allowLisp;
    }

    public IReadOnlySet<string> Denylist { get; }

    public IReadOnlySet<string> Allowlist { get; }

    public bool AllowLisp { get; }

    public static SendCommandPolicy FromEnvironment(Func<string, string?>? read = null)
    {
        read ??= Environment.GetEnvironmentVariable;
        string? lisp = read(EnvAllowLisp)?.Trim().ToLowerInvariant();
        return new SendCommandPolicy(
            SplitNames(read(EnvDeny)),
            SplitNames(read(EnvAllow)),
            lisp is "1" or "true" or "yes" or "on");
    }

    public void CheckText(string text, bool firstIsCommand = true)
    {
        if (!AllowLisp && text.Contains('(', StringComparison.Ordinal))
        {
            throw new HostApprovalException(
                "E_COMMAND_DENIED",
                "LISP expressions are refused by host command policy (set " + EnvAllowLisp + "=1 to allow)");
        }

        string[] tokens = text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        foreach (string token in tokens)
        {
            string name = Normalize(token);
            if (Denylist.Contains(name))
            {
                throw new HostApprovalException("E_COMMAND_DENIED", "command '" + name + "' is on the host command denylist");
            }
        }

        if (firstIsCommand && Allowlist.Count > 0 && tokens.Length > 0)
        {
            string first = Normalize(tokens[0]);
            if (!Allowlist.Contains(first))
            {
                throw new HostApprovalException("E_COMMAND_DENIED", "command '" + first + "' is not on the " + EnvAllow + " allowlist");
            }
        }
    }

    public void Check(string method, JsonElement parameters)
    {
        if (method is not ("host.send_command" or "xicad.run"))
        {
            return;
        }

        bool isObject = parameters.ValueKind == JsonValueKind.Object;
        if (method == "host.send_command")
        {
            JsonElement command = default;
            bool found = isObject
                && (parameters.TryGetProperty("command", out command) || parameters.TryGetProperty("cmd", out command));
            CheckText(found ? TextOf(command) : string.Empty);
            return;
        }

        CheckText(isObject && parameters.TryGetProperty("command", out JsonElement cmd) ? TextOf(cmd) : string.Empty);
        if (isObject && parameters.TryGetProperty("args", out JsonElement args) && args.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement arg in args.EnumerateArray())
            {
                CheckText(TextOf(arg), firstIsCommand: false);
            }
        }
    }

    private static string TextOf(JsonElement element) => element.ValueKind switch
    {
        JsonValueKind.String => element.GetString() ?? string.Empty,
        JsonValueKind.Null or JsonValueKind.Undefined => string.Empty,
        _ => element.GetRawText(),
    };

    private static string Normalize(string token) => token.TrimStart(PrefixChars.ToCharArray()).ToUpperInvariant();

    private static IEnumerable<string> SplitNames(string? raw) =>
        string.IsNullOrWhiteSpace(raw)
            ? Array.Empty<string>()
            : raw.Split(new[] { ',', ';', ' ', '\t', '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
}

/// <summary>Language-neutral canonical JSON for operation digests (see host_approval.canonical_json).</summary>
public static class CanonicalJson
{
    public static string Serialize(JsonElement element)
    {
        var builder = new StringBuilder();
        Write(builder, element);
        return builder.ToString();
    }

    public static void Write(StringBuilder builder, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                WriteObject(builder, element, skipKey: null);
                break;
            case JsonValueKind.Array:
                builder.Append('[');
                bool first = true;
                foreach (JsonElement item in element.EnumerateArray())
                {
                    if (!first) builder.Append(',');
                    first = false;
                    Write(builder, item);
                }

                builder.Append(']');
                break;
            case JsonValueKind.String:
                WriteString(builder, element.GetString() ?? string.Empty);
                break;
            case JsonValueKind.Number:
                builder.Append(Number(element));
                break;
            case JsonValueKind.True:
                builder.Append("true");
                break;
            case JsonValueKind.False:
                builder.Append("false");
                break;
            default:
                builder.Append("null");
                break;
        }
    }

    public static void WriteObject(StringBuilder builder, JsonElement element, string? skipKey)
    {
        builder.Append('{');
        if (element.ValueKind == JsonValueKind.Object)
        {
            // Last duplicate wins, as in Python's json.loads.
            var props = new SortedDictionary<string, JsonElement>(StringComparer.Ordinal);
            foreach (JsonProperty prop in element.EnumerateObject())
            {
                if (skipKey is not null && string.Equals(prop.Name, skipKey, StringComparison.Ordinal)) continue;
                props[prop.Name] = prop.Value;
            }

            bool first = true;
            foreach (var kv in props)
            {
                if (!first) builder.Append(',');
                first = false;
                WriteString(builder, kv.Key);
                builder.Append(':');
                Write(builder, kv.Value);
            }
        }

        builder.Append('}');
    }

    public static void WriteString(StringBuilder builder, string value)
    {
        builder.Append('"');
        foreach (char ch in value)
        {
            if (ch == '"') builder.Append("\\\"");
            else if (ch == '\\') builder.Append("\\\\");
            else if (ch >= 0x20 && ch < 0x7F) builder.Append(ch);
            else builder.Append("\\u").Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture));
        }

        builder.Append('"');
    }

    private static string Number(JsonElement element)
    {
        // A literal without fraction or exponent is an integer (Python json.loads -> int);
        // anything else is a double (Python float), even if it is integral like 1e16.
        string raw = element.GetRawText();
        if (raw.IndexOfAny(new[] { '.', 'e', 'E' }) < 0)
        {
            return element.TryGetInt64(out long integer)
                ? integer.ToString(CultureInfo.InvariantCulture)
                : raw;
        }

        double value = element.GetDouble();
        if (Math.Floor(value) == value && Math.Abs(value) < 9007199254740992d)
        {
            return ((long)value).ToString(CultureInfo.InvariantCulture);
        }

        return ShortestRepr(value);
    }

    /// <summary>
    /// Shortest round-trip digits laid out like Python's float repr: fixed notation when the
    /// decimal point position is in (-4, 16], otherwise d.ddde+XX. .NET's "R" picks a different
    /// switch-over point, so only its digits are used.
    /// </summary>
    private static string ShortestRepr(double value)
    {
        string r = value.ToString("R", CultureInfo.InvariantCulture);
        bool negative = r.StartsWith('-');
        if (negative) r = r[1..];
        int exp = 0;
        int e = r.IndexOfAny(new[] { 'E', 'e' });
        if (e >= 0)
        {
            exp = int.Parse(r[(e + 1)..], NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
            r = r[..e];
        }

        int dot = r.IndexOf('.');
        string intPart = dot >= 0 ? r[..dot] : r;
        string fracPart = dot >= 0 ? r[(dot + 1)..] : string.Empty;
        string digits = intPart + fracPart;
        int decpt = intPart.Length + exp;
        int lead = 0;
        while (lead < digits.Length - 1 && digits[lead] == '0') { lead++; decpt--; }
        digits = digits[lead..].TrimEnd('0');
        if (digits.Length == 0) { digits = "0"; decpt = 1; }

        string body;
        if (decpt > -4 && decpt <= 16)
        {
            if (decpt <= 0) body = "0." + new string('0', -decpt) + digits;
            else if (decpt >= digits.Length) body = digits + new string('0', decpt - digits.Length) + ".0";
            else body = digits[..decpt] + "." + digits[decpt..];
        }
        else
        {
            int exponent = decpt - 1;
            string mantissa = digits.Length == 1 ? digits : digits[0] + "." + digits[1..];
            body = mantissa + "e" + (exponent < 0 ? "-" : "+") + Math.Abs(exponent).ToString("00", CultureInfo.InvariantCulture);
        }

        return negative ? "-" + body : body;
    }
}
