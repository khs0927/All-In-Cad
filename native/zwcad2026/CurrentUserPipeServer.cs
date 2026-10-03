using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;
using System.Threading;

namespace AllInCad.ZWCAD2026
{
    internal sealed class CurrentUserPipeServer : IDisposable
    {
        private const int MaximumMessageBytes = 8 * 1024 * 1024;
        private static readonly HashSet<string> KnownMethods = new HashSet<string>(StringComparer.Ordinal)
        {
            "system.ping", "host.context", "host.capabilities", "document.snapshot",
            "entity.read", "plan.execute", "verification.capture"
        };
        private readonly string pipeName;
        private readonly Func<string, Dictionary<string, object?>> dispatcher;
        private readonly string? requiredSessionToken;
        private readonly object pipeSync = new object();
        private volatile bool stopping;
        private volatile bool listenerReady;
        private volatile string? lastListenerError;
        private volatile NamedPipeServerStream? activePipe;
        private Thread? listener;

        public bool IsListening
        {
            get { return listenerReady; }
        }

        public string? LastListenerError
        {
            get { return lastListenerError; }
        }

        public CurrentUserPipeServer(
            string pipeName,
            Func<string, Dictionary<string, object?>> dispatcher,
            string? requiredSessionToken)
        {
            if (string.IsNullOrWhiteSpace(pipeName))
            {
                throw new ArgumentException("Pipe name is required.", nameof(pipeName));
            }

            this.pipeName = pipeName;
            this.dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
            this.requiredSessionToken = requiredSessionToken;
        }

        public void Start()
        {
            if (listener != null)
            {
                throw new InvalidOperationException("Pipe server is already running.");
            }

            listener = new Thread(Listen)
            {
                IsBackground = true,
                Name = "All-In-Cad ZWCAD pipe listener"
            };
            listener.Start();
        }

        private void Listen()
        {
            while (!stopping)
            {
                NamedPipeServerStream? pipe = null;
                try
                {
                    pipe = CreatePipe();
                    lock (pipeSync)
                    {
                        if (stopping)
                        {
                            pipe.Dispose();
                            return;
                        }

                        activePipe = pipe;
                        listenerReady = true;
                    }
                    lastListenerError = null;
                    pipe.WaitForConnection();
                    ProcessClient(pipe);
                }
                catch (IOException error)
                {
                    if (!stopping)
                    {
                        lastListenerError = error.GetType().Name + ": " + error.Message;
                    }
                }
                catch (Exception error)
                {
                    if (!stopping)
                    {
                        lastListenerError = error.GetType().Name + ": " + error.Message;
                    }
                }
                finally
                {
                    lock (pipeSync)
                    {
                        listenerReady = false;
                        if (ReferenceEquals(activePipe, pipe))
                        {
                            activePipe = null;
                        }
                    }
                    if (pipe != null)
                    {
                        pipe.Dispose();
                    }
                }

                if (!stopping && lastListenerError != null)
                {
                    Thread.Sleep(250);
                }
            }
        }

        private NamedPipeServerStream CreatePipe()
        {
            var identity = WindowsIdentity.GetCurrent();
            var userSid = identity.User;
            if (userSid == null)
            {
                throw new InvalidOperationException("Could not resolve the current Windows user SID.");
            }

            var security = new PipeSecurity();
            security.SetAccessRuleProtection(true, false);
            security.AddAccessRule(new PipeAccessRule(
                userSid,
                PipeAccessRights.FullControl,
                AccessControlType.Allow));

            return new NamedPipeServerStream(
                pipeName,
                PipeDirection.InOut,
                1,
                PipeTransmissionMode.Byte,
                PipeOptions.None,
                65536,
                65536,
                security);
        }

        private void ProcessClient(Stream stream)
        {
            var serializer = new JavaScriptSerializer
            {
                MaxJsonLength = MaximumMessageBytes,
                RecursionLimit = 100
            };

            while (!stopping)
            {
                byte[]? body = ReadFrame(stream);
                if (body == null)
                {
                    return;
                }

                byte[] response;
                Guid requestId;
                string? method;
                string? suppliedToken;
                try
                {
                    var request = ParseRequest(serializer, body, out requestId, out method, out suppliedToken);
                    if (!SessionTokenMatches(suppliedToken))
                    {
                        response = Failure(serializer, requestId, "E_SESSION_AUTH", "Invalid or missing session token.");
                    }
                    else
                    {
                        try
                        {
                            response = Success(serializer, requestId, dispatcher(method!));
                        }
                        catch (RpcDispatchException error)
                        {
                            response = Failure(serializer, requestId, error.Code, error.Message);
                        }
                        catch (NotSupportedException error)
                        {
                            response = Failure(serializer, requestId, "E_METHOD_DISABLED", error.Message);
                        }
                        catch (Exception error)
                        {
                            response = Failure(serializer, requestId, "E_HOST_ERROR", error.Message);
                        }
                    }
                }
                catch (RpcDispatchException error)
                {
                    response = Failure(serializer, error.RequestId, error.Code, error.Message);
                }
                catch (Exception error)
                {
                    response = Failure(serializer, Guid.Empty, "E_PROTOCOL", error.Message);
                }

                WriteFrame(stream, response);
            }
        }

        private static Dictionary<string, object> ParseRequest(
            JavaScriptSerializer serializer,
            byte[] body,
            out Guid requestId,
            out string? method,
            out string? sessionToken)
        {
            requestId = Guid.Empty;
            method = null;
            sessionToken = null;
            var json = new UTF8Encoding(false, true).GetString(body);
            var request = serializer.DeserializeObject(json) as Dictionary<string, object>;
            if (request == null)
            {
                throw new RpcDispatchException("E_PROTOCOL", "Request JSON root must be an object.", Guid.Empty);
            }

            var allowed = new HashSet<string>(StringComparer.Ordinal)
            {
                "protocol",
                "request_id",
                "method",
                "params",
                "session_token"
            };
            foreach (var key in request.Keys)
            {
                if (!allowed.Contains(key))
                {
                    throw new RpcDispatchException("E_PROTOCOL", "Unexpected request field: " + key, Guid.Empty);
                }
            }

            var protocol = request.ContainsKey("protocol") ? request["protocol"] as string : "aic.native/1";
            if (!string.Equals(protocol, "aic.native/1", StringComparison.Ordinal))
            {
                throw new RpcDispatchException("E_PROTOCOL", "Unsupported protocol.", Guid.Empty);
            }

            if (request.TryGetValue("request_id", out var idValue))
            {
                var idText = idValue as string;
                if (idText == null || !Guid.TryParse(idText, out requestId))
                {
                    throw new RpcDispatchException("E_PROTOCOL", "request_id must be a UUID.", Guid.Empty);
                }
            }
            else
            {
                requestId = Guid.NewGuid();
            }

            method = request.TryGetValue("method", out var methodValue) ? methodValue as string : null;
            if (string.IsNullOrWhiteSpace(method))
            {
                throw new RpcDispatchException("E_PROTOCOL", "method is required.", requestId);
            }

            if (!KnownMethods.Contains(method!))
            {
                throw new RpcDispatchException("E_PROTOCOL", "Unknown native method: " + method, requestId);
            }

            if (request.TryGetValue("params", out var paramsValue)
                && !(paramsValue is Dictionary<string, object>))
            {
                throw new RpcDispatchException("E_PROTOCOL", "params must be an object.", requestId);
            }

            if (request.TryGetValue("session_token", out var tokenValue))
            {
                if (tokenValue != null && !(tokenValue is string))
                {
                    throw new RpcDispatchException("E_PROTOCOL", "session_token must be a string or null.", requestId);
                }

                sessionToken = tokenValue as string;
            }

            return request;
        }

        private bool SessionTokenMatches(string? supplied)
        {
            if (requiredSessionToken == null)
            {
                return true;
            }

            if (supplied == null)
            {
                return false;
            }

            var expected = Encoding.UTF8.GetBytes(requiredSessionToken);
            var actual = Encoding.UTF8.GetBytes(supplied);
            if (expected.Length != actual.Length)
            {
                return false;
            }

            var difference = 0;
            for (var index = 0; index < expected.Length; index++)
            {
                difference |= expected[index] ^ actual[index];
            }

            return difference == 0;
        }

        private static byte[] Success(
            JavaScriptSerializer serializer,
            Guid requestId,
            Dictionary<string, object?> result)
        {
            return Serialize(serializer, new Dictionary<string, object?>
            {
                ["protocol"] = "aic.native/1",
                ["request_id"] = requestId.ToString("D"),
                ["ok"] = true,
                ["result"] = result
            });
        }

        private static byte[] Failure(
            JavaScriptSerializer serializer,
            Guid requestId,
            string code,
            string message)
        {
            return Serialize(serializer, new Dictionary<string, object?>
            {
                ["protocol"] = "aic.native/1",
                ["request_id"] = requestId.ToString("D"),
                ["ok"] = false,
                ["error"] = new Dictionary<string, object?>
                {
                    ["code"] = code,
                    ["message"] = message,
                    ["details"] = new Dictionary<string, object?>()
                }
            });
        }

        private static byte[] Serialize(JavaScriptSerializer serializer, object value)
        {
            var body = new UTF8Encoding(false).GetBytes(serializer.Serialize(value));
            if (body.Length <= 0 || body.Length > MaximumMessageBytes)
            {
                throw new InvalidDataException("Response frame size is invalid.");
            }

            return body;
        }

        private static byte[]? ReadFrame(Stream stream)
        {
            var header = new byte[4];
            if (!ReadExact(stream, header, 0, header.Length))
            {
                return null;
            }

            var length = (uint)(header[0]
                | (header[1] << 8)
                | (header[2] << 16)
                | (header[3] << 24));
            if (length == 0 || length > MaximumMessageBytes)
            {
                throw new InvalidDataException("Invalid message length: " + length);
            }

            var body = new byte[(int)length];
            return ReadExact(stream, body, 0, body.Length) ? body : null;
        }

        private static bool ReadExact(Stream stream, byte[] buffer, int offset, int count)
        {
            var readTotal = 0;
            while (readTotal < count)
            {
                var read = stream.Read(buffer, offset + readTotal, count - readTotal);
                if (read == 0)
                {
                    return false;
                }

                readTotal += read;
            }

            return true;
        }

        private static void WriteFrame(Stream stream, byte[] body)
        {
            var length = (uint)body.Length;
            var header = new[]
            {
                (byte)(length & 0xff),
                (byte)((length >> 8) & 0xff),
                (byte)((length >> 16) & 0xff),
                (byte)((length >> 24) & 0xff)
            };
            stream.Write(header, 0, header.Length);
            stream.Write(body, 0, body.Length);
            stream.Flush();
        }

        public void Dispose()
        {
            stopping = true;
            lock (pipeSync)
            {
                var pipe = activePipe;
                activePipe = null;
                listenerReady = false;
                if (pipe != null)
                {
                    pipe.Dispose();
                }
            }

            var currentListener = listener;
            if (currentListener != null && currentListener != Thread.CurrentThread)
            {
                currentListener.Join(TimeSpan.FromMilliseconds(250));
            }
        }
    }

    internal sealed class RpcDispatchException : Exception
    {
        public string Code { get; private set; }
        public Guid RequestId { get; private set; }

        public RpcDispatchException(string code, string message, Guid requestId)
            : base(message)
        {
            Code = code;
            RequestId = requestId;
        }
    }
}






