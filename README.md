# E-Sys CLI

A JSON command-line interface for agents and scripts using an existing E-Sys installation. Includes offline XML inspection, local dataset manifests, verified identity reads, captures and experimental TAL planning/execution.

**Alpha software. Vehicle programming has not been validated on a live vehicle with this CLI.** Offline tests and a native E-Sys version probe do not establish that flashing, coding or calibration will work. Fault-memory reading and clearing are **not implemented**. Native commands still require separately installed E-Sys and compatible datasets.

## Install

Python 3.11 or newer. Offline commands work on Windows and Linux. Native E-Sys workflows require Windows.

```powershell
python -m pip install "git+https://github.com/Solarthis/esys-cli.git"
esys capabilities
```

Or clone and install locally:

```powershell
git clone https://github.com/Solarthis/esys-cli.git
cd esys-cli
python -m pip install .
python -m esys_cli capabilities
```

The source checkout also provides `./esys.ps1`; set `ESYS_CLI_PYTHON` if Python is not found. The wheel installs the `esys` console command, not the PowerShell script. Runtime dependencies are Python's standard library only.

## External datasets

The public repository contains code and synthetic tests under MIT. Supply your own legitimately obtained E-Sys installation and PSdZData. No proprietary datasets are distributed or converted into MIT data. See [THIRD_PARTY.md](THIRD_PARTY.md).

Keep datasets outside your checkout. `--data-root` identifies the **parent** of `psdzdata`:

```text
<data-root>/
  psdzdata/
    mainseries/<series>/<project>/
    swe/btld/
    swe/swfl/
    swe/cafd/
```

```powershell
esys --data-root "D:\BMWData" dataset inspect
esys --data-root "D:\BMWData" projects
esys --data-root "D:\BMWData" dataset manifest --output "D:\PrivateEvidence\baseline.json"
esys --data-root "D:\BMWData" dataset verify --manifest "D:\PrivateEvidence\baseline.json"
```

`inspect` counts files and logical bytes without hashing. `manifest` records every file's relative path, byte size and SHA-256. `verify` checks the complete inventory, including extra and missing files, and reads every file again. Manifests remain usable after moving an unchanged dataset. Neither command modifies dataset files. Existing manifest files are never overwritten; outputs must be outside the dataset. Keep the dataset idle during hashing: this is not a filesystem snapshot. Metadata and inventory changes detected during hashing fail the operation.

Manifests contain filenames and hashes, not firmware. Keep real manifests private unless you have reviewed their contents and sharing rights. Symlinks and directory junctions within datasets are rejected by inventory commands. Transparent Windows file compression is supported; the hashes cover the bytes applications read. There is no compression, download or dataset-upload command.

## Configuration

Explicit global options take precedence over environment variables. Put global options **before** the command.

| Option | Environment | Default |
| --- | --- | --- |
| `--esys-root` | `ESYS_ROOT` | `C:\EC-Apps\ESG\E-Sys` |
| `--data-root` | `ESYS_DATA_ROOT` | `C:\Data` |
| `--jobs-root` | `ESYS_JOBS_ROOT` | Per-user state directory + `jobs` |
| `--timeout` | — | 120 seconds |

State lives in `%LOCALAPPDATA%\esys-cli` on Windows and `$XDG_STATE_HOME/esys-cli` (or `~/.local/state/esys-cli`) on Linux. Session locks are shared across installations for the same OS user, independently of `--jobs-root`. They do not coordinate separate computers or OS accounts. Do not run another diagnostic client concurrently. Native process checks also reject detected E-Sys sessions.

## Agent contract

Start with `esys capabilities`. Commands return one JSON document on stdout; help is plain text. Native logs go to job files. Check both the exit code and `status`, and inspect referenced evidence. Profiles, plans, captures and logs can contain vehicle identities and local paths; keep them out of Git.

| Exit | Meaning |
| --- | --- |
| 0 | Command completed successfully within its stated verification scope |
| 2 | Invalid input, dataset mismatch, missing dependency or local setup failure |
| 3 | Native read/calculation/process failure |
| 4 | Write outcome unverified; retain the job and lock, never retry automatically |
| 5 | Session locked |

`job <job.json>` reads stored evidence. A historical record is not proof of current vehicle state.

## Offline inspection and connection setup

```powershell
esys inspect fa ./private/FA.xml
esys inspect svt ./private/SVT.xml
esys inspect tal ./private/TAL.xml
esys doctor
esys doctor --native
esys profile init --fa ./private/FA.xml --series F025 --project F025_26_03_550_V_004_000_000 --host 192.0.2.10 --output ./profiles/vehicle.json
```

The series, project and documentation-only IP above are examples, not values for your vehicle. Select an installed compatible project and actual interface address. Profile creation reads identity from a file; connected workflows read a fresh FA and check that identity before continuing. `doctor --native` probes the installed version without connecting to a vehicle. The native adapter was characterized against E-Sys 3.39.1; compatibility with other versions is unverified.

## Connected reads

```powershell
esys capture --profile ./profiles/vehicle.json
esys capture --profile ./profiles/vehicle.json --include-ncd
esys read vin --profile ./profiles/vehicle.json
esys read svt --profile ./profiles/vehicle.json
esys read software-version --profile ./profiles/vehicle.json --ecu 0x29
esys read ncd --profile ./profiles/vehicle.json
esys read vcm-master --profile ./profiles/vehicle.json --vcm-type FA
```

Available read kinds: `fa`, `svt`, `ncd`, `vcm-master`, `vcm-backup`, `vin`, `software-version`. Firmware versions come from the fresh SVT. Optional `read ncd --svt <file>` scopes the request against fresh state. NCD success establishes that at least one nonempty NCD was returned; it does not establish complete backup coverage. VCM success checks returned artifacts, not their full semantic contents.

## Experimental programming

`calculate-tal` **connects to the vehicle** in the characterized E-Sys version. It takes `--profile`, `--fa`, `--svt-ist`, `--svt-soll` and `--filter`.

`plan` is offline. Supply `--profile`, `--fa`, `--svt-ist`, `--svt-soll`, `--tal`, repeatable `--allow-ecu` and a new `--output` filename. It restricts operations to `blFlash`, `swDeploy` and `cdDeploy`, checks hardware and ECU scope, requires changed target components to be covered by the correct operation, and hashes input files and required software payloads. Unsupported transitions and same-state recoding are rejected conservatively. Plans from earlier CLI versions must be regenerated.

`execute` requires `--plan`, the exact `--accept-plan` hash and `--power-confirmed`. Power confirmation is an operator attestation that suitable stable programming power is in place; this CLI does not measure it. Before writing, it checks fresh identity and scoped ECU state, revalidates the plan, and verifies staged inputs. Success requires the native Finished marker and a matching fresh scoped SVT. This does not verify calibration, fault clearing, every ECU, or all vendor side effects. Vendor TAL transformation, retry and VCM/MSM defaults have not been exhaustively characterized.

Do not treat a successful plan as authorization to flash. Review the TAL and native behavior for your installation. A write timeout stops observation, not the native process. Code 4 retains the session lock and evidence. Inspect the native process, logs and actual vehicle state before recovery; there is no automatic unlock or retry command.

## Development

```powershell
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --wheel-dir dist .
```

Tests use synthetic data and fake vehicle responses. CI runs on Windows/Linux with Python 3.11 and 3.13; it does not connect to vehicles. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

Licensed under [MIT](LICENSE). External vendor software/data retain their own terms.
