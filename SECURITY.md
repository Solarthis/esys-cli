# Security and operational reports

For vulnerabilities, use GitHub's private vulnerability reporting for this repository when available. Do not publish vehicle identifiers, credentials, keys, private dataset inventories or unredacted logs in issues.

This is alpha tooling with experimental vehicle programming. No release is certified for live programming. Dataset hashes establish equality with a chosen local baseline; they do not establish authenticity, authorization or vehicle compatibility. Treat manifests and plans as local evidence, not vendor signatures.

If a write returns code 4, preserve its job directory and session lock. The native process may still be running. Determine its state and inspect the actual vehicle before any recovery action. Never automatically retry a write or remove a lock just because it appears old.
