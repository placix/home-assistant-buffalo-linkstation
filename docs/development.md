# Development notes

## Adopted repository conventions

The neighboring `home-assistant-botslab360` and
`home-assistant-discovery-monitor` projects were inspected before this project
was initialized. Their useful shared conventions are retained here:

- HACS's single-integration `custom_components/<domain>` layout
- typed config-entry runtime data and one coordinator/client per entry
- config-flow-only setup, unload support, English source strings, and German
  translations
- `pyproject.toml`-based Ruff/pytest configuration with strict linting
- separate HACS, hassfest, lint, formatting, and pytest CI validation
- integration and package versions beginning together at `0.1.0`
- a draft release workflow that runs only for an explicitly pushed version tag

Current Home Assistant guidance takes precedence: the integration uses the
shared `aiohttp` session, typed coordinator entities, reauth/reconfigure flows,
stable unique IDs, device-centric entity naming, and translated exceptions.

## Protocol boundaries

Only observed/source-evidenced methods are used for polling. Modern JSON-RPC
and LS-WVL legacy CGI are separate clients behind one protocol-neutral client
contract. Configuration tries only authentication and read-only identity calls,
then stores the selected protocol in the config entry. Power methods are
invoked exclusively by button entities; no capability probe can invoke either
method. Read-only calls retry connection/timeout failures at most once, while
destructive calls are never retried after connection loss or a timeout.

Request IDs are random and checked against the response. Sensitive request
parameters and response bodies are never logged. The Home Assistant-owned HTTP
session is not closed during entry unload.

### Verified JSON-RPC behavior from the local firmware bundles

All 43 LS220D and 41 LS420D JavaScript files were inventoried and compared.
Thirty-five files are byte-identical between the collections. The Ext JS
framework and generic widget files contain no NAS
protocol behavior; the API-relevant calls and response fields are concentrated
in the common utility, login, header, progress, shutdown, template, and share
tree files. The findings below are independent descriptions, not copied vendor
source.

- Requests are JSON-RPC 2.0 POSTs to `/nasapi/` with an object in `params`.
  Authenticated calls carry `sid` inside that object. The browser implementation
  uses string request IDs. The common request timeout is 45 seconds on LS220D
  and 20 seconds on LS420D; these are frontend choices, not API capabilities.
- `Auth.login` takes `username` and `password`. An optional `timeout: "0"` is
  added only for the Web UI's persistent-session choice, so the integration
  deliberately omits it. Successful responses contain `sid` and `uid`.
- Error code `-119` is the specifically handled invalid-session response. The
  client reauthenticates only for that code (or HTTP 401/403), rather than
  guessing from error-message text. Codes `-130` and `-32603` trigger a Web UI
  database-recovery path and are not authentication failures.
- `get_portal_info_authneed` provides `hostname`, `product_name`,
  `fw_version_major`, `fw_version_minor`, `serial_number`, `cpu_status`,
  `errors`, `informations`, `interfaces`, and `disks`. The advertised firmware
  version is taken from `fw_version_major`; the UI treats the minor value as an
  additional build component.
- Each portal disk record uses `device_name`, `usage`, `used_size`,
  `total_size`, and `used_percentage`. The UI converts sizes by dividing by
  1024 twice before display, establishing KiB input and GiB output semantics for
  this firmware family.
- `device.getlist` returns a `devices` array. Confirmed fields include
  `device_id`, `device_name`, `device_type`, `status`, `usage`, `interface`,
  `slaves_device_id`, `holders_device_id`, and `progress`. RAID devices have
  `device_type: "array"`. The Web UI's status precedence is: broken, rebuild,
  degrade, reshape, resync, repair, removed, formatting, checking,
  disconnected, unformatted, then fine.
- `get_service_status` is not a system-health endpoint. It requires
  `service_name`, returns that name under `service_status`, and uses the least
  significant bit as the enabled state. It was removed from general polling.
- The share-list method is `share.getlist`, not `share.get_list`.
- `getNasFeature` is session-independent and must be sent with `params: {}`.
  Both firmware finalizers invoke it before authenticated portal loading. The
  retained bundles omit the static metadata containing its `isNotUseSID`
  entry, but the empty browser request works while SID-bearing hardware calls
  fail on both verified models.
- `System.shutdown` takes no operation-specific parameters; the common request
  layer adds only `sid`. The shutdown page checks for an administrator role
  before calling it. The integration does not send a role request because the
  NAS remains the authority and will reject an unauthorized command.
- The progress code polls portal/job endpoints to observe shutdown/restart
  transitions. It does not establish the restart command name or parameters.
  No `System.reboot` call was found anywhere in the supplied files. Restart has
  since been exercised through Home Assistant on real hardware, but remains
  explicitly controlled and disabled by default.

The static API metadata referenced by the Web UI was not supplied. Therefore,
apart from independently confirmed `getNasFeature`, the complete list of
methods that permit an empty SID and the firmware's full error-code catalog
cannot be confirmed from these files.

See [firmware-comparison.md](firmware-comparison.md) for the complete
cross-model method, field, error, feature, and confidence analysis.
See [hardware-validation.md](hardware-validation.md) for the credential-safe,
read-only Windows capture workflow and local fixture validation.

### Verified shutdown capability

`System.shutdown` has been successfully tested on LS220D firmware 1.86 and
LS420D firmware 1.88. A missing shutdown option defaults to enabled for those
exact pairs and disabled for other pairs. Once stored, the user's explicit
setting always wins, including disabling shutdown on verified hardware.

`SUPPORT_SHUTDOWN_FROMWEB` must not be treated as an authoritative JSON-RPC
capability indicator. Both completed hardware captures report the flag as `0`
while authenticated `System.shutdown` works on both devices. The integration
therefore uses the hardware-verified model/firmware allowlist for defaults and
never uses that flag to enable or disable the shutdown API.

The captures also confirm feature-level product strings `LS220D(SANJO)` and
`LS420D(LS-WM)`, while the portal reports `LS220D` and `LS420D`. Firmware is
reported as separate major and minor/build fields (`1.86`/`0.07` and
`1.88`/`0.03`) and displayed by the Web UI with a hyphen. Capability matching
normalizes only these confirmed aliases and full version forms. It does not
use prefix matching, discard unknown build suffixes, or enable other models.

## Adding hardware evidence

Capture response *shape* with values sanitized. Remove passwords, cookies,
session IDs, usernames, serials, share names, hostnames, IP/MAC addresses, and
user-created text. Add the sanitized fixture to `tests/fixtures/`, write parsing
tests first, then add entities only for fields with verified meaning and units.

Do not infer capacity units from field names and never discover a feature by
executing a power operation.

## Local Buffalo firmware references

Original Buffalo Web UI JavaScript is kept in model-specific local directories:

- `reference/LS220D/` for LS220D firmware 1.86
- `reference/LS420D/` for LS420D firmware 1.88
- `reference/LS-WVL/` for LS-WVL firmware 1.75

Each model preserves the firmware Web UI hierarchy below its directory. Page
scripts are stored under `js/`, shared Web UI logic under `js/common/`, Ext JS
files under `js/ext/` (including `js/ext/locale/`), and UI extensions under
their original `js/ux/` component directories. A file absent from one firmware
is not copied from the other model or replaced with a placeholder.

LS-WVL 1.75 uses a separate legacy Web UI layout rooted at `static/`, not the
newer models' `js/` layout. Its own HTML and dynamic loader references establish
the hierarchy, including framework files under `static/ext/`, authentication
and configuration files under `static/authentication/` and `static/cfg/`, and
feature pages under `static/menus/`. In that legacy tree, user/group management
is under `static/menus/usrGrpMnt/`, shared utilities are under
`static/menus/utils/`, and system settings are under
`static/menus/system/settings/`. The LS-WVL collection remains local-only
vendor reference material. The integration implements its independently
documented `/dynamic.pl` behavior in a separate client; no vendor source is
copied into the integration.

The models remain separate even when files happen to be identical. This makes
firmware comparisons possible without erasing model-specific implementations
or provenance. The entire `reference/` tree is ignored by Git and excluded from
HACS/release distributions; it must never be committed, published, packaged,
or redistributed. These vendor files are local research material only, and
their source contents must not be copied into the integration or documentation.

When present, inspect `utils.js`, `login_main_bis.js`,
`shutdown_onready.js`, `header_main.js`, and `progress.js` first. Record useful
protocol findings independently in this document or tests using original
descriptions and sanitized request/response shapes. If a relevant file is
absent, continue from the documented protocol and label assumptions as
unverified.
