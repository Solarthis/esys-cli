# Agent entry point

Read README.md and run `python -m esys_cli capabilities` before selecting a workflow. stdout is JSON except help; inspect exit status and referenced evidence.

Keep datasets and vehicle artifacts external. Never add firmware, vendor libraries, licenses/keys, real profiles, captures, manifests or logs to this repository. Tests must generate synthetic fixtures.

Run `python -m unittest discover -s tests -v` after behavior changes. Test connected workflows through the fake vehicle boundary, never against a live vehicle during development.

Independent `diag` commands need no vendor software or datasets. Read docs/diagnostics.md first. `diag demo` uses loopback only. Fault clearing requires a fresh plan, exact acceptance hash and operator stationary attestation; never infer that attestation. Preserve original faults and uncertain-write locks. Do not turn simulator acceptance into a live-vehicle compatibility claim. Run `python tools/check_release.py` before publishing.

Programming is experimental. Use explicit profiles and fresh captures. Execution requires a validated plan, its exact acceptance hash and an explicit operator power attestation. An agent must not invent this attestation. A code-4 result is an unverified write: inspect evidence; do not automatically retry or delete the lock.
