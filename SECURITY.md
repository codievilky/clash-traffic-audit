# Security and privacy

The service reads connection metadata and stores local aggregates. It does not decrypt HTTPS, capture raw packets, upload telemetry or read subscription credentials.

HTTP endpoints are read-only and bound to IPv4 loopback. The server checks the Host header, sends no permissive CORS headers and uses a restrictive content security policy. Runtime files use a restrictive umask. Potential CSV formulas are escaped.

This is not a public or multi-user dashboard. Other software running as the same OS user can access the data. Do not publish the loopback endpoint or give untrusted programs access to the Mihomo socket. Use SSH forwarding for your own remote access.

Databases and exports can reveal usage patterns. Keep them out of public issues and commits. `.gitignore` is a convenience, not a substitute for reviewing staged files.

If GitHub private vulnerability reporting is available, use it for sensitive reports. Otherwise open a minimal issue requesting a private channel without posting secrets or private data. There is currently no guaranteed security response SLA.
