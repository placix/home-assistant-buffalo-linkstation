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

## LS-WVL firmware 1.75 legacy CGI diagnostic

LS-WVL firmware 1.75 uses the legacy `/dynamic.pl` CGI interface, not the
JSON-RPC client used by LS220D and LS420D. The standalone diagnostic does not
enable LS-WVL in Home Assistant and does not import or alter the modern API
client.

The firmware source and an actual Firefox Network Monitor trace confirm that
login is a form-encoded POST to `/dynamic.pl` with exactly
`bufaction=verifyLogin`, `user=<username>`, and `password=<password>`. The
source then creates a `webui_session_<username>` cookie from the returned SID
and page mode plus the UI language. The diagnostic uses the source-defined
English fallback language. It now supports this native login, checks the
response's boolean success value and required session fields, then requires a
successful `validateSession` response before any data request.

The hostname and username are prompted interactively and the password uses a
masked prompt. Credentials and cookies remain only in process memory: they are
not accepted on the command line, printed, or written to either output file.
The HTTP session uses an in-memory cookie jar for both server-set cookies and
the Web UI's documented `webui_session_*` cookie.

### Manual-cookie fallback

Native username/password login is the default. A fresh, temporary authenticated
Web UI cookie remains available as a diagnostic fallback with
`--manual-cookie`. Use a browser on a trusted local network:

1. Open the LS-WVL Web UI and sign in normally.
2. Open the browser developer tools and select **Network**.
3. Reload a read-only page such as System Status.
4. Select a successful `/dynamic.pl` request and inspect its **Request Headers**.
5. From the `Cookie` header, copy only the single pair whose name begins with
   `webui_session_`, including its name and value in `name=value` form. Do not
   copy unrelated cookies.
6. Run the diagnostic promptly because the firmware UI gives this cookie a
   ten-minute client-side lifetime. Close the browser session or log out after
   the controlled capture.

The source-derived diagnostic currently uses local HTTP, matching the legacy
firmware workflow. A session cookie sent through HTTP is not encrypted. Run it
only on a trusted, isolated LAN; do not route the request across the Internet or
an untrusted wireless network. HTTPS must be captured and verified separately
before it can be offered without silently changing the protocol.

Do not save the cookie in a text file, shell history, command-line argument,
screen recording, issue, or chat message. The cookie name itself contains the
Web UI username and must also be treated as private.

The fallback cookie is entered through a masked prompt, held only in memory,
never printed, and never persisted. Never include an actual username, password,
cookie value, SID, hostname, IP address, MAC address, or serial number in a bug
report or browser trace.

### Run the legacy capture

From the repository root in PowerShell:

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe tools\capture_legacy_readonly.py
```

If the earlier cookie-based `ls-wvl_1_75.json` capture is still present, first
rename it to a distinct archival name within the Git-ignored
`reference/captures/` directory. Preserve it; the tool intentionally refuses to
move, delete, or overwrite any existing capture.

An optional non-secret timeout can be supplied:

```powershell
.\.venv\Scripts\python.exe tools\capture_legacy_readonly.py --timeout 30
```

The script prompts for the NAS hostname/IP, username, and masked password.
Review its fixed action list and confirm before any network request is sent. To
use the fallback instead, add `--manual-cookie` and enter the temporary cookie
at its masked prompt. `--help`, imports, setup, and automated tests do not
contact a NAS.

In native mode, the first request is the browser-confirmed login POST. The next
request is the source-confirmed read-only `validateSession` action. Manual mode
starts with validation. If login or validation fails, none of the data actions
run. After successful validation, these source-confirmed reads run
independently:

- GET `getFeatures`
- GET `getDiskAllList`
- GET `getArrayList`
- POST `getArrayMaintenance`
- GET `getArrayMiscInfo`
- POST `getUPSSettings`
- POST `getOperationStatus`

The GET/POST choices match the Ext UI implementation. Every request is fixed to
`http://<entered-host>/dynamic.pl` and contains only its allowlisted
`bufaction`. There is no arbitrary action argument. In particular, the tool has
no path to `shutRestartLS`, any `set*` action, disk check/format/erase, RAID
modification, firmware update, user management, or permission changes.

The tool does not automatically renew a session or retry login after rejection;
the server-side renewal and expiration contract has not been verified. Start a
new controlled run with fresh credentials or a fresh fallback cookie instead.

The tool does not call the system-status action because the referenced
`status/status_vars.js` file is missing and its literal `bufaction` is unknown.
It also excludes indexed `getRootSettings...` calls until a browser capture
establishes the correct index and response for the desired summary page.

Results are written without conversion or rounding to:

- `reference/captures/ls-wvl_1_75.json`
- `reference/captures/ls-wvl_1_75_diagnostic.json`, only when a request fails

Each successful method records the HTTP status and sanitized legacy response
envelope. Failed actions are stored only in the diagnostic report. Other reads
continue after a timeout, network error, malformed response, HTTP error, or CGI
error so successful evidence is retained. A capture is marked
`"complete": true` only after session validation and every listed data action
succeeds. Existing capture or diagnostic files are never overwritten.

The sanitizer redacts session/authentication values, usernames, hostnames,
serial numbers, network identifiers, addresses, email addresses and common
free-form identifying fields. It preserves numbers and technical fields such as
disk/array status, raw sizes, percentages, modes and progress. Firmware can add
unknown fields, so manually inspect every saved string before sharing anything.

Run the focused mocked tests without a device:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_capture_legacy_tool.py -q
```

The first LS-WVL capture is complete. Session validation and all seven data
actions returned HTTP 200 with `success: true`. It confirms the
`LS-WVL(KOUGYOKU)` product string, one RAID1 array with status `ok`, both internal
disks in the normal-member list, a disconnected unused USB slot, and
1,369,523,620 of 1,937,364,900 reported KB used (70.69%). It also confirms an
inactive UPS configuration with raw status `ups_status4`; the meaning of that
symbol remains unknown.

Firmware 1.75 is operator-supplied capture metadata because none of these seven
actions returns a firmware version. The capture does not validate shutdown,
reboot, degraded RAID states, disk failures, whether `KB` means 1000 or 1024
bytes, the missing system-status action, or operation inside a running Home
Assistant installation.

### LS-WVL integration behavior

Native username/password login has also been successfully tested on real
LS-WVL hardware. The integration automatically selects legacy CGI using only
authentication, session validation, and read-only identity requests, then
stores that protocol per config entry. Each entry owns an isolated in-memory
cookie jar, so modern and legacy devices can coexist.

The integration exposes the captured storage numbers unchanged with the unit
`KB (reported)`, plus the captured percentage. It exposes RAID mode/status and
normalized internal membership while retaining raw values internally. The
disconnected USB placeholder is excluded from internal-disk health. It does not
create firmware, CPU, fan, SMART, or system-error values that these actions do
not provide.

LS-WVL shutdown and reboot remain disabled by default. Their request forms are
implemented from firmware evidence but are not hardware-verified. Detection,
setup and polling never call them.

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
as a separate Home Assistant entity. Reboot has been exercised through Home
Assistant on real hardware, but remains disabled by default and is never
automatically probed. Its Web UI feature flag does not control availability.
