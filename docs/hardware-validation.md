# Read-only hardware validation

The diagnostic tool captures only the three read-only results needed to verify
the integration against LS220D firmware 1.86 and LS420D firmware 1.88. Running
tests or importing the tool never contacts a NAS. Network access begins only
after an operator starts the script, enters credentials interactively, reviews
the method list, and answers the confirmation prompt.

## Safety boundaries

The fixed method set is:

- `get_portal_info_authneed`
- `getNasFeature`
- `device.getlist`

The tool has no generic method argument and never invokes shutdown, reboot,
service control, configuration, or another mutating method. The password is
read with a masked secure prompt. Host, username, and password are not accepted
as command-line arguments and are not logged.

Responses are sanitized in memory before being written. SID/UID values,
credentials, serial numbers, host and user names, workgroups/domains, IP and
MAC addresses, email addresses, free-form messages, and similar identifying
fields are redacted. Numeric values—including raw `used_size`, `total_size`,
`used_percentage`, and progress values—are preserved without conversion or
rounding. Review every capture manually before sharing it because firmware may
introduce fields unknown to the sanitizer.

Captures are written separately to:

- `reference/captures/ls220d_1_86.json`
- `reference/captures/ls420d_1_88.json`

After authentication, each allowlisted method is called independently. The
console prints an information line before each call and a success line only
after that call succeeds. A JSON-RPC failure includes the method-local error
code and a sanitized message; the tool then continues with the remaining
read-only methods.

Successful responses are saved even when another method fails. Such a file has
`"complete": false`, the process returns a failure exit code, and a separate
diagnostic report records only the failed method names, error codes, error
types, and sanitized messages:

- `reference/captures/ls220d_1_86_diagnostic.json`
- `reference/captures/ls420d_1_88_diagnostic.json`

A complete capture has `"complete": true`. Never use a partial capture as
evidence that all three methods or their response structures were validated.
The tool refuses to overwrite an existing capture or diagnostic report. The
complete `reference/` tree is ignored by Git and excluded from release/HACS
artifacts.

## Windows instructions

Open PowerShell and run the repository setup script:

```powershell
cd home-assistant-buffalo-linkstation
.\setup.ps1
```

The script prefers the Windows Python launcher, verifies the minimum Python
version declared in `pyproject.toml`, creates `.venv` when needed, and safely
reuses a valid existing environment. It installs `aiohttp`, which is required
by the API client and capture tool, along with Windows-compatible lint and test
tools. Home Assistant itself and its Home Assistant-specific pytest plugin are
excluded on Windows because the complete runtime is not required for a
read-only capture and is not a supported native Windows development
dependency.

The script uses `.venv\Scripts\python.exe` directly, so activation is not
required. It validates the API import and the capture tool's `--help` command;
these checks do not create an HTTP session or contact a NAS. Running
`setup.ps1` again reuses `.venv` and skips dependency installation when the
declarations have not changed.

If PowerShell blocks the script, use a process-scoped exception in the current
window. Do not weaken the user or machine policy permanently:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
```

Run one model at a time. Supplying `--model` is optional and contains no
credential:

```powershell
.\.venv\Scripts\python.exe tools\capture_readonly.py --model LS220D
.\.venv\Scripts\python.exe tools\capture_readonly.py --model LS420D
```

The script prompts for the hostname/IP, username, and password. Confirm the
displayed three-method allowlist before proceeding. To use a longer request
timeout without exposing credentials, add a safe option such as
`--timeout 45`.

If the tool reports a partial capture, review both the capture and its matching
`_diagnostic.json` file locally. The console and diagnostic file never contain
credentials, session IDs, user IDs, or raw response bodies. Error messages are
passed through the same identifier sanitizer before display or storage.

### Retry only `getNasFeature`

The original hardware runs established that portal and device calls succeeded
while the SID-bearing `getNasFeature` request failed. To retry only the
corrected session-independent feature request, run:

```powershell
.\.venv\Scripts\python.exe tools\capture_readonly.py --model LS220D --retry-features
.\.venv\Scripts\python.exe tools\capture_readonly.py --model LS420D --retry-features
```

This mode asks only for the NAS hostname/IP; it does not request credentials or
authenticate. It sends only `getNasFeature` with an empty parameter object. It
requires an existing sanitized partial capture where that is the sole missing
method. On success it writes a new `*_completed.json` file and marks it complete
only after all three required method results are present. The original partial
capture and its diagnostic report remain byte-for-byte unchanged. Existing
completed output is never overwritten.

After each capture:

1. Open the JSON locally and check every string for identifying information.
2. Confirm the model and firmware metadata match the physical NAS.
3. Do not move the capture outside `reference/` or add it to Git.
4. Run the focused capture and parsing tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_capture_tool.py tests\test_parsing.py -q
```

When a real ignored fixture exists, these tests verify the response structure,
identity fields, raw-capacity conversion to GiB, system-error interpretation,
and RAID status/problem parsing. Without a capture, the two hardware-dependent
cases are skipped rather than replaced with invented response values.

## Hardware-verified results

Completed sanitized captures were validated for both supported devices:

| Field | LS220D 1.86 | LS420D 1.88 |
| --- | --- | --- |
| Required methods | All three present | All three present |
| CPU status | `normal_state` | `normal_state` |
| System errors / information messages | Empty / empty | Empty / empty |
| Internal disk status | Two SATA disks, both `fine` | Two SATA disks, both `fine` |
| Array | RAID1, `fine` | RAID0, `fine` |
| Raw used / total | 34,380 / 2,913,320,916 KiB | 34,484 / 3,874,730,480 KiB |
| Converted used / total | 0.0 / 2,778.4 GiB | 0.0 / 3,695.2 GiB |
| Calculated utilization | 0.0% | 0.0% |
| RAID / shutdown / reboot flags | 1 / 0 / 1 | 1 / 0 / 1 |

Both device lists also contain an unused USB disk placeholder whose status is
`disconnected`. This is not an internal-disk failure. The disk-status sensor
therefore excludes records with `interface: "usb"` and reports `fine` for both
captured systems. RAID status is derived only from `device_type: "array"` and
is `fine`; the RAID-problem binary sensor is off.

The firmware, CPU, disk, RAID, capacity, and utilization sensors have values in
both captures. Empty `errors` and `informations` arrays intentionally do not
create text sensors, while the system-error binary sensor is available and off.
All coordinator entities become unavailable after a failed polling update and
resume from the next successful snapshot; an unreachable device is never
reported as safely powered off.

## Information to compare manually

- Compare raw portal disk values with the Buffalo UI capacity display. The
  source-derived expectation is KiB divided by 1024 twice to obtain GiB.
- Compare every array's raw `status` and device relationships with the RAID
  state shown by the Web UI.
- Confirm `errors` and `informations` are arrays and that an empty `errors`
  array represents no reported system error.
- Record feature values, especially shutdown/reboot flags, as observations only;
  do not test capability by sending a power operation.
- Report response fields or types that differ between the models without
  copying identifiers or vendor JavaScript.

The healthy captures do not establish backend responses for degraded arrays,
rebuild/resync progress, failed disks, or non-empty error/information lists.
Those mappings remain source-informed and covered by synthetic regression
tests. RAID level is validated in the device data but is not currently exposed
as a separate Home Assistant entity. Reboot remains opt-in because an
advertised feature flag is not proof that the power operation is safe.
