# Independent diagnostics

`diag` speaks UDS directly over BMW ENET/HSFZ or ISO DoIP using Python sockets. It does not load E-Sys, Java, EDIABAS, PSdZData, ISTA databases or vendor DLLs. It is implemented as original code under this repository's MIT license.

Implemented services are VIN (DID F190), explicit ReadDataByIdentifier, ReadDTCInformation by status mask, and ClearDiagnosticInformation for all DTC groups **inside each explicitly selected ECU**. Fault codes are raw three-byte identifiers with decoded status bits; manufacturer fault descriptions and repair instructions are not included. No functional/broadcast clear, automatic ECU scanning, arbitrary raw services, security-access bypass or firmware download is exposed.

## Before connecting

Use a compatible interface, a parked vehicle and an appropriate ignition/power state for that vehicle. Close other diagnostic clients. The software does not measure speed, power or ignition state. Profiles specify an actual IPv4 endpoint, transport and physical ECU addresses; do not infer a DoIP logical address from an HSFZ address. Find these values from trusted vehicle/interface information. Unknown or rejected ECU services stop the command rather than triggering guessed procedures.

HSFZ uses short transactions in the default diagnostic session, normally on TCP 6801. DoIP normally uses TCP 13400 with routing activation. Defaults are tester F4 for HSFZ and 0E00 for DoIP, DoIP version 2 and activation type 00. Override only with known appropriate values. TLS, authenticated routing, non-default diagnostic sessions and HSFZ gateway-specific control/heartbeat variants are not implemented. Gates requesting authentication are not bypassed.

## Create a profile without datasets

Example only; replace the documentation IP, synthetic VIN and ECU addresses with your verified values:

```powershell
esys diag profile init --transport hsfz --host 192.0.2.10 --vin WBA00000000000001 --identity-ecu 10 --ecu 29 --output ./profiles/diagnostics.json
```

Use `--transport doip` and your interface's logical addresses for DoIP. Optional arguments: `--port`, `--source`, `--protocol-version 2|3`, `--activation-type`. ECU, DID, source and activation values are hexadecimal, with or without `0x`. Repeat `--ecu` to name multiple intended ECUs (maximum 32). The identity ECU must support DID F190; it can differ from the target ECU, allowing a gateway to establish vehicle identity for a module that does not store a VIN.

Profile creation makes no network request. An expected VIN is required because connected operations verify vehicle identity; it is not a discovered/live identity until the fresh read succeeds. This consistency check is not cryptographic authentication of the ECU or network.

## Read vehicle information and faults

```powershell
esys diag vin --profile ./profiles/diagnostics.json
esys diag read-did --profile ./profiles/diagnostics.json --ecu 29 --did F189
esys diag faults --profile ./profiles/diagnostics.json
```

Only use DIDs supported by that ECU. `read-did` returns raw hex, not a guessed software version or engineering-unit conversion. `faults` requests status mask FF and reports the ECU's availability mask and every returned record. A code alone is not a diagnosis.

Put `--request-timeout` immediately after `diag`, for example `esys diag --request-timeout 10 faults ...`. Each transaction has one overall deadline, including pending responses; it is not extended indefinitely. Transport acknowledgements, negative responses and malformed replies never count as diagnostic success. No automatic retries or reconnects occur.

## Review and clear faults

Clearing erases diagnostic history; it does not repair the underlying fault. This workflow saves the selected ECUs' original codes and status bits first. Freeze-frame snapshots and extended diagnostic records are not backed up by this command; use suitable tooling first if you need those records.

```powershell
esys diag clear-plan --profile ./profiles/diagnostics.json --output ./profiles/clear-plan.json
esys diag clear --plan ./profiles/clear-plan.json --accept-plan <plan_hash_from_previous_result> --vehicle-stationary
```

Review the plan's VIN, endpoint, ECU scope and original faults before accepting its exact hash. The plan expires after five minutes. `--vehicle-stationary` is a real operator attestation, not something an agent may infer from a general instruction to clear faults. It is unnecessary for the loopback demo, where the simulated ECU is never a physical vehicle.

Before the first clear, the CLI reads fresh VIN and faults for every scoped ECU and compares them with the plan. Changed state or an expired/edited plan rejects the operation. For each ECU it sends one `14 FF FF FF` request, requires a `54` UDS response, reads faults back, and saves the result. The final VIN is checked again. A failed/uncertain write stops subsequent ECUs.

Success reports `clear_acknowledged`, `all_faults_absent`, `remaining_fault_count`, and complete before/after records. An accepted clear can still leave faults: read these fields. Status bits meaning tests have not completed (bits 4/6) are not counted as failed/pending/confirmed/warning faults. An immediate clean read-back does not prove repairs, calibration or that a fault will not return after driving.

If communication, interruption or evidence writing fails after a clear is attempted, the result is exit **4**, `write_unverified`. Keep the job and lock. Do not resend, delete the lock automatically, or assume no change happened. Inspect the vehicle and saved evidence before recovery. A definitive ECU rejection is handled conservatively after a write attempt as well.

Jobs include private `job.json`, raw `transport.jsonl`, accepted plan and original fault snapshot. Keep these off GitHub. Locks are shared with native CLI operations for the same expected VIN and OS user. They cannot exclude unrelated applications, other computers, OS users or profiles using a different expected VIN. Close other clients before using either backend.

## What remains vehicle-specific

This is independent basic diagnostics, not a complete replacement for every E-Sys/ISTA function. Full firmware programming, variant coding, sensor initialization and calibration can require ECU-specific software, definitions, procedures and authorization. None are invented from generic UDS support. The existing native backend remains available for its separately documented experimental programming path. No protected manufacturer dataset is made open source by this release.

## Verification and references

Both transports have literal wire-vector tests, fragmented/coalesced-frame tests and a real TCP simulator. The CLI demo exercises VIN, fault capture, plan acceptance, clear and read-back with missing vendor/data roots. Tests cover wrong identity/scope, plan tampering/expiry, pending/negative responses, missing acknowledgements, persistent faults, interrupted writes and shared locks. **No physical vehicle has been acceptance-tested for this release.**

Protocol field/service references (no third-party implementation is bundled):

- [Scapy HSFZ API](https://scapy.readthedocs.io/en/latest/api/scapy.contrib.automotive.bmw.hsfz.html)
- [Rawenet HSFZ protocol research](https://github.com/rawmind0/rawenet/blob/master/docs/hsfz-protocol.md)
- [udsoncan UDS service documentation](https://udsoncan.readthedocs.io/en/latest/udsoncan/services.html)
- [DoIP client message documentation](https://python-doipclient.readthedocs.io/en/latest/messages.html)
