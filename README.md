# Buffalo LinkStation for Home Assistant

Local monitoring and explicit power controls for compatible Buffalo LinkStation
NAS devices through their JSON-RPC API. No Buffalo cloud service is used.

This is an early, model-conscious integration. Buffalo firmware responses vary,
so an entity is created only when its source field is present. It never probes
power controls during discovery or polling.

## Features

- UI configuration and one independent config entry per NAS
- Automatic login, in-memory session handling, reauthentication after an
  explicit session rejection, request timeouts, and sanitized logs
- Coordinator polling with recovery after temporary network failures
- Device registry metadata: model, hostname, firmware, serial number, and URL
- Firmware, CPU, system message/error, disk/RAID status, and storage capacity
  and utilization sensors when the corresponding fields are returned
- Reachability, system-error, and RAID-problem binary sensors when supported
- Serialized shutdown and experimental restart buttons for Home Assistant
  automations
- Configurable polling interval, timeout, and explicit power-control overrides
- English and German translations

The examined Web UI treats portal `used_size` and `total_size` values as KiB:
it divides each by 1024 twice before presenting a capacity. The integration
therefore exposes converted GiB and derives aggregate utilization from those
same raw totals.

## Compatibility

| Model | Firmware | Monitoring | Shutdown | Restart | Evidence |
| --- | --- | --- | --- | --- | --- |
| LS220D | 1.86 | **Verified**: portal, features, disks, RAID1, health, and capacity | **Verified** | Unverified; opt-in only | Sanitized read-only capture plus successful hardware shutdown test |
| LS420D | 1.88 | **Verified**: portal, features, disks, RAID0, health, and capacity | **Verified** | Unverified; opt-in only | Sanitized read-only capture plus successful hardware shutdown test |
| LS-WXL | Unknown | Not supported by this API client | Not tested | Not tested | Older third-party integration uses a different Python library |

Other models are not assumed compatible. Please attach sanitized JSON response
shapes to a bug report before adding a model to this table. Never test support
by sending a shutdown command during discovery.

The compatibility table records firmware/API behavior and sanitized hardware
responses. It does not mean this initial release has already been exercised in
a running Home Assistant installation. Home Assistant-specific automated tests
run in Linux CI; live integration testing remains part of the pre-release
evaluation.

## Installation

### HACS custom repository

1. In HACS, open **Integrations**, then the menu and **Custom repositories**.
2. Add `https://github.com/placix/home-assistant-buffalo-linkstation` as an
   **Integration** repository.
3. Download **Buffalo LinkStation** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**, search for
   **Buffalo LinkStation**, and enter the NAS hostname/IP, username, and
   password.

For manual installation, copy `custom_components/buffalo_linkstation` into Home
Assistant's `config/custom_components/` directory and restart Home Assistant.

Use a DHCP reservation or stable hostname. The endpoint is always
`http://<host>/nasapi/`; credentials are never put into the URL.

## Entities and controls

The integration creates only sensors backed by fields actually returned by the
device. Missing fields are normal and do not fail setup. A failed update makes
coordinator entities unavailable, so old measurements are not presented as
current. In particular, an unreachable NAS is **unavailable**, not "powered
off"; loss of network connectivity does not prove a clean shutdown.

The shutdown button is available without an opt-in on the two hardware-verified
model/firmware combinations: LS220D 1.86 and LS420D 1.88. Other combinations
remain opt-in. `SUPPORT_SHUTDOWN_FROMWEB` is deliberately not used to decide
API capability: both completed hardware captures report
`SUPPORT_SHUTDOWN_FROMWEB = 0`, yet authenticated `System.shutdown` was
successfully tested on both devices. The flag describes Web UI behavior and is
not an authoritative statement about whether the underlying JSON-RPC method works.
It is also never used as permission to expose shutdown on an unverified model.
Restart remains experimental and explicit opt-in. No `System.reboot` invocation
was present in the supplied firmware JavaScript, so the Web UI feature flag is
not used as proof that this client method is supported. Both controls can be
reviewed under the integration's **Configure** options.

Power operations occur only after a button press or a standard Home Assistant
`button.press` action. Operations are serialized. A timeout is never retried,
because the NAS may already have accepted the command.

## UPS automation example

Home Assistant's NUT integration should monitor the UPS. This integration only
provides the standard NAS shutdown button. The example waits, confirms the UPS
is still on battery, and uses an input boolean to request shutdown once per
outage. Create `input_boolean.linkstation_shutdown_requested` first and replace
the entity IDs.

```yaml
alias: Shut down LinkStations during an extended outage
mode: single
triggers:
  - trigger: state
    entity_id: sensor.ups_status
    to: "OB"
actions:
  - delay: "00:05:00"
  - condition: state
    entity_id: sensor.ups_status
    state: "OB"
  - condition: state
    entity_id: input_boolean.linkstation_shutdown_requested
    state: "off"
  - action: input_boolean.turn_on
    target:
      entity_id: input_boolean.linkstation_shutdown_requested
  - action: button.press
    target:
      entity_id:
        - button.office_nas_shut_down
        - button.backup_nas_shut_down
  - action: notify.notify
    data:
      message: "LinkStation shutdown requested because the UPS remained on battery."
```

Reset the helper in a separate automation only after mains power has been
stable long enough for the outage to be considered over. Do not automatically
restart a NAS when power returns: mains may return without the UPS output being
power-cycled, and LinkStation wake behavior depends on its own power settings.

## Troubleshooting

- Confirm Home Assistant can reach `http://<host>/nasapi/` on the LAN.
- Confirm the NAS web interface accepts the same credentials.
- Increase the timeout under **Configure** on a slow or busy NAS.
- For a shutdown button that is unexpectedly unavailable, open
  **Developer Tools → States** and inspect only that button's diagnostic
  attributes. `reported_model` and `reported_firmware` show the API identity;
  `recognized_model` and `recognized_firmware` show the safety-normalized
  values. `availability_reason` distinguishes an unavailable coordinator, a
  running power operation, missing identity fields, and an unverified pair.
  These attributes never include credentials, session IDs, hostnames, network
  addresses, serial numbers, or response bodies.
- If entities are missing, the firmware likely did not return their known
  fields. Enable debug logging, reproduce one update, and share only sanitized
  field names/types—not response values that could identify your device.
- After a firmware update, reauthenticate if Home Assistant reports an
  authentication repair.

```yaml
logger:
  logs:
    custom_components.buffalo_linkstation: debug
```

The integration never logs passwords, request parameters, session IDs, cookies,
or response bodies.

## Security

Many older Buffalo firmwares expose this API over unencrypted HTTP. Use the
integration only on a trusted local network, isolate untrusted clients, and use
a dedicated NAS account if the firmware permits one with the required access.
Never forward the NAS API or web-management port to the public internet.

Home Assistant stores config-entry credentials in its protected configuration
storage. Session IDs remain in memory only.

## Known limitations

- The implementation is covered by mocked response tests; no automated test
  talks to real hardware or sends a real power command.
- Sanitized local captures validate the healthy-state response structures for
  both models. They remain Git-ignored and are not distributed with the
  integration, so CI skips capture-backed cases when those files are absent.
- Degraded, rebuilding, missing-disk, non-empty system-message, and non-empty
  system-error responses remain covered by synthetic tests rather than captured
  hardware states.
- HTTPS is not offered by the observed endpoint and certificate behavior has
  not been established.
- Shares are fetched to validate API/session behavior but are not exposed as
  entities because names may be sensitive and are not health measurements.

## Manual hardware test checklist

Perform read-only checks first on each LS220D (1.86) and LS420D (1.88):

1. Back up the NAS configuration and confirm disks/RAID are healthy.
2. Add the NAS by IP and by hostname in separate trials; verify duplicate
   prevention by serial number where the serial is returned.
3. Verify model, firmware, hostname, serial, and configuration URL in the
   device registry without sharing identifiers in logs/screenshots.
4. Compare each created sensor with the Buffalo UI; record sanitized response
   keys and confirmed units for missing storage/disk/RAID data.
5. Enter a wrong password, then reauthenticate successfully.
6. Expire the web session and confirm the next read authenticates again.
7. Disconnect the network, confirm entities become unavailable (not safely
   off), reconnect, and confirm recovery without reloading.
8. Configure two NAS devices and confirm their data and controls remain
   independent.
9. Do not test **Restart** until its method and parameters are independently
   confirmed for that exact firmware.
10. During a separate approved maintenance window with storage idle and a
    backup available, press **Shut down** once and confirm a clean shutdown.
    Do not treat connection loss alone as proof.
11. Run the disabled-by-default UPS automation with shortened timing and mock
    or supervised UPS states; confirm cancellation when mains returns and
    one request per outage.

## Development

### Windows setup and read-only capture

From PowerShell, use the repository setup script. It selects a compatible
Python installation (preferring the Windows `py` launcher), creates or reuses
`.venv`, and installs the capture dependencies plus the development tools that
work on Windows. It deliberately does not install the complete Home Assistant
runtime on Windows.

```powershell
cd home-assistant-buffalo-linkstation
.\setup.ps1

.\.venv\Scripts\python.exe tools\capture_readonly.py --model LS220D
```

For an LS420D, use:

```powershell
.\.venv\Scripts\python.exe tools\capture_readonly.py --model LS420D
```

If the current PowerShell execution policy blocks local scripts, change it for
that PowerShell process only, then run setup:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
```

This process-scoped setting ends when that PowerShell window closes and does
not change the machine or user policy. The setup validation imports the API
client and runs the capture tool's `--help`; neither operation contacts a NAS.
During a real operator-approved capture, the tool reports each allowlisted
method separately. If one fails, successful sanitized responses are retained
in an explicitly incomplete capture and sanitized failure metadata is written
to a separate ignored diagnostic file. See the hardware-validation guide for
the filenames and interpretation.
For captures where only the formerly SID-bearing `getNasFeature` request
failed, `--retry-features` safely retries that one session-independent method
and creates a new completed copy without modifying the original evidence.

### Full test environment

The complete Home Assistant test stack is installed on supported non-Windows
environments from the same dependency file:

```bash
python -m pip install -r requirements_test.txt
ruff check .
ruff format --check .
python -m pytest -q -p no:cacheprovider
```

See [docs/development.md](docs/development.md) for architecture conventions and
hardware-fixture guidance. The Windows-safe capture procedure is documented in
[docs/hardware-validation.md](docs/hardware-validation.md).

## License

MIT. This repository does not include Buffalo vendor JavaScript or code from
the older `hass-linkstation-addon` project.
