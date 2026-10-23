# LS-WVL firmware 1.75 legacy API analysis

## Scope and evidence labels

This document describes the local management interface exposed by the Buffalo
LinkStation LS-WVL firmware 1.75 web UI. It is an investigation artifact, not an
implementation specification. No request was sent to a NAS while preparing it.

The following labels are used throughout:

- **Source-confirmed**: directly shown by a collected firmware JavaScript or HTML
  file.
- **Hardware-confirmed**: observed in the sanitized read-only capture from an
  LS-WVL reported by the operator as running firmware 1.75.
- **Inferred**: a likely consequence of the source, but not completely defined by
  the available files.
- **Unknown**: the collection does not establish the behavior.

The source references below are paths inside the local, Git-ignored
`reference/LS-WVL/` tree. They identify evidence without reproducing Buffalo's
source. Line numbers refer to the collected files and may change if a different
firmware bundle is collected.

## Hardware capture validation

The ignored `reference/captures/ls-wvl_1_75.json` capture was validated on
October 24, 2026. It has `complete: true`, reports successful temporary-cookie
authentication, and contains every required read-only action. Each action
returned HTTP 200 with a boolean `success: true` and a `data` array.

| Action | Hardware-confirmed result |
|---|---|
| `validateSession` | Successful; returned language and update state |
| `getFeatures` | Successful feature ID/value list |
| `getDiskAllList` | Successful; array volume, two member disks and one disconnected USB slot |
| `getArrayList` | Successful; one RAID1 array |
| `getArrayMaintenance` | Successful; RAID scan configuration |
| `getArrayMiscInfo` | Successful; RAID failure/synchronization configuration |
| `getUPSSettings` | Successful; UPS configuration/status record |
| `getOperationStatus` | Successful; no operation active |

The legacy envelope is not completely uniform: successful responses use
`errors: []`, `errors: null`, and, for the idle operation response,
`errors: [null]`. A parser must use the boolean `success` field as the primary
outcome and must not treat a truthy `errors` list containing only null as a
failure.

### Hardware-confirmed identity and features

`getFeatures` returned `SERIES_NAME=LinkStation` and
`PRODUCT_NAME=LS-WVL(KOUGYOKU)`. This confirms that model aliases/suffixes need
normalization if the integration displays `LS-WVL` as the family name. The same
response reported `PRODUCT_CAPACITY=4.0T`, two internal disk slots, one array,
RAID, RAID0 and RAID1 support, USB UPS support, and no serial UPS support.

Firmware `1.75` is capture metadata supplied when the diagnostic was run. None
of the captured actions returned an authoritative firmware-version field. The
device firmware therefore remains operator-confirmed, not API-confirmed. The
missing system-status action is still required to verify the `version` response
field.

The observed Web UI feature values include
`SUPPORT_SHUTDOWN_FROMWEB=0` and `SUPPORT_REBOOT_FROMWEB=1`. As with the modern
devices, these are UI feature observations rather than proof that either power
operation succeeds. No LS-WVL power operation was executed.

### Hardware-confirmed storage and RAID

The array-volume record from `getDiskAllList` returned formatted strings:

| Field | Observed value |
|---|---:|
| `amountUsed` | `1,369,523,620 KB` |
| `capacity` | `1,937,364,900 KB` |
| `percentUsed` | `70.69 %` |
| `fileFormat` | `XFS` |

The same values in `getArrayList` are one-element string arrays without the unit
suffix: `sizeUsedRaw=["1,369,523,620"]`,
`sizeAllRaw=["1,937,364,900"]`, and `sizeUsedRate=["70.69"]`. Dividing the raw
used value by the raw total value produces 70.690019 percent, which confirms
that the reported percentage is internally consistent.

The API/UI explicitly reports the unit label `KB`. The capture cannot establish
whether Buffalo means decimal kilobytes or 1024-byte units. The integration
therefore preserves the raw integer and labels it `KB (reported)` until browser
UI comparison or backend evidence resolves that byte interpretation. Applying
the modern client's KiB-to-GiB conversion would be an unverified assumption.

The single array reported:

- `arrayMode=raid1`
- `arrayStatus=ok`
- `arrayFormat=["xfs"]`
- `diskNormal=["disk1", "disk2"]`
- `diskError=null`
- no spare disks

The two physical disk records use `status=array1`, which indicates membership,
not an independent SMART/health state. Their capacity fields are `-`; usable
capacity is represented by the array-volume record. The unused USB slot reports
`status=disconnect` and must not be interpreted as an internal disk failure.
Only the healthy RAID value `ok` and membership value `array1` are
hardware-confirmed. Degraded, rebuilding and failed values remain unverified.

### Hardware-confirmed system and UPS data

`getOperationStatus` returned null operation, task, progress and elapsed-time
fields, confirming only that no tracked maintenance operation was active.
`getArrayMaintenance` reported RAID scanning enabled with a weekly schedule, and
`getArrayMiscInfo` reported high synchronization speed and RAID-failure shutdown
enabled. These are configuration values, not current health errors.

No captured action returned hostname, firmware version, fan state, CPU state,
system warnings, structured errors or informational messages. Those modern
integration entities therefore cannot be populated for LS-WVL and are omitted.

The UPS response reported `syncUps=off`, `upsStatus=ups_status4`, an empty
`connectType`, and `behavior`, `recovery`, and `shutdown` all set to `off`.
`time` and `wait` were null. The symbolic meaning of `ups_status4` is not
hardware-confirmed because the localization dictionary remains unavailable; it
must be preserved as a raw status rather than translated to connected or
disconnected by assumption.

## Firmware architecture overview

**Source-confirmed:** this firmware uses a same-origin CGI-style endpoint,
`/dynamic.pl`, rather than JSON-RPC 2.0 or `/nasapi/`. Operations are selected by
the form/query parameter `bufaction`; some operations have additional parameters.
The browser decodes JSON responses. The common response shape is:

```text
success: boolean
errors: array
data: array
```

This is only a structural description, not a captured vendor response.

Evidence:

- `static/authentication/login_utils.js:15-24` submits `verifyLogin` and decodes
  JSON.
- `static/menus/utils/common.js:17-63` documents and processes the
  `success`/`errors`/`data` envelope.
- `static/root/root.js:2689-2697` creates the feature store for `/dynamic.pl` with
  `bufaction=getFeatures`.
- `static/menus/system/disk/diskMgmt_diskBasic.js:302-354` and
  `static/menus/system/disk/diskMgmt_array.js:126-182` define JSON stores for disk
  and array data.

Both GET and POST are used:

- Ext data stores and explicit `Ext.Ajax.request` calls default to GET unless a
  method is supplied. The status, feature, disk and RAID list reads fall into
  this category. **Source-confirmed.**
- Ext form submissions use POST with URL-encoded form fields. Login, shutdown
  and reboot use this mechanism. **Source-confirmed.**
- The bundled Ext library adds `X-Requested-With: XMLHttpRequest` and a cache
  buster to eligible GET requests. These are browser-library behaviors, not
  demonstrated server requirements. **Source-confirmed client behavior; server
  requirement unknown.**
- Same-origin browser requests automatically include applicable cookies.
  **Inferred** to be how the backend receives the session, because ordinary
  calls do not contain a SID parameter.

No JSON-RPC request object, JSON-RPC method identifier, or request ID is used by
the examined legacy UI. The existing modern API transport is therefore not
wire-compatible.

## Authentication and session handling

### Login sequence

1. The login UI obtains the values of Ext components `user` and `password`.
   (`static/authentication/login_utils.js:10-13`)
2. It submits the login form to `/dynamic.pl` with
   `bufaction=verifyLogin`. (`login_utils.js:15-19`) The bundled Ext form action
   defaults to POST, and an actual Firefox Network Monitor trace confirmed a
   form-encoded POST containing exactly `bufaction`, `user`, and `password`.
   **Source-confirmed and hardware-confirmed.**
3. A successful response is expected to contain `data[0].sid` and
   `data[0].pageMode`. (`login_utils.js:50-56`)
4. The browser deletes older Web UI cookies and creates
   `webui_session_<username>`. Its value contains the SID, language and page mode
   separated by underscores. (`login_utils.js:55-78`)
5. The browser redirects to `/static/root.html`. (`login_utils.js:276-281`)

The cookie has path `/` and a ten-minute client-side expiration. The code does
not add Domain or Secure attributes; because JavaScript creates it, it cannot be
HttpOnly. No SameSite attribute is specified. These observations describe the
firmware UI and are not recommendations for new code.

The serialized credential names `user` and `password` are
**hardware-confirmed**. No additional login field was present in the observed
request. The missing form HTML is therefore no longer a blocker for diagnostic
authentication, although it remains unavailable for reconstructing the entire
login page.

There is no challenge-response, digest or password hashing step in the collected
login utility. This does not prove that another uncollected login script cannot
add one. Plain HTTP would expose login material on the local network, so a future
client must warn users and must never log request bodies.

### Session validation and renewal

Feature pages first request `bufaction=validateSession`, then issue their data
requests after success. Examples are
`static/menus/status/system/status_system.js:1-42`,
`static/menus/system/disk/diskMgmt_main.js:4-85`, and
`static/menus/system/powerManagement/powerManagement_main.js:9-127`.

`resetCookie()` refreshes the browser cookie's ten-minute expiration while
retaining its SID and page mode, optionally taking a language value from a
response (`login_utils.js:104-139`). This is a client-side sliding expiration.
Whether the server has the same timeout or renews server state on every request
is **unknown**.

The ordinary API requests examined do not add `sid` to the form/query
parameters. The session is therefore **inferred** to be authenticated by the
`webui_session_*` cookie. The diagnostic retains all response cookies in one
in-memory cookie jar. It preserves a server-provided `webui_session_*` cookie;
when none is provided, it reproduces the JavaScript's documented
SID-language-page-mode value, using the source-defined English fallback, and
then requires `validateSession` to succeed. Whether the server sets that cookie
itself remains **unknown**.

### Expiration, errors and logout

The common failure handler parses JSON and displays localized identifiers from
the `errors` array. If no error identifier is present, it treats the failure as
session expiration (`static/menus/utils/common.js:17-63`, `84-126`). Expiration
ultimately deletes the Web UI cookie and redirects to `/static/index.html`
(`login_utils.js:261-281`). HTTP status handling and a numeric server error-code
taxonomy are **unknown**.

The login failure path presents a generic invalid-username-or-password message
for any failed form submission (`login_utils.js:27-45`). It does not expose a
distinct authentication error code.

The visible logout link deletes client cookies and navigates upward
(`static/root/root.js:2398-2406`). No server-side logout operation was found.
Whether the backend SID remains usable until server expiration is **unknown**.

## Read-only operations and response fields

The following table lists operations relevant to monitoring. `GET` reflects the
request generated by the examined Ext code. All operations use `/dynamic.pl`.
None has been executed against hardware.

| Purpose | Method and confirmed parameters | Expected response fields | Confidence |
|---|---|---|---|
| Session check | GET, `bufaction=validateSession` | Common envelope; page code only checks success | Source-confirmed |
| Feature/model discovery | GET, `bufaction=getFeatures` | `data[]` records with `id`, `value` | Source-confirmed |
| Root summary/warnings | GET, `bufaction=getRootSettings<menu index>` | `data[0].generalInfo[]` name/value records, optionally including `system_warning`; menu-specific records may follow | Source-confirmed; index-dependent contents unknown |
| System status | GET, `bufaction=<missing constant>` | `hostName`, `modelName`, `version`, date/time, `timezone`, `workgroup`, `ntp`, `emailAlert`, `fanStatus` | Fields source-confirmed; action name unknown |
| Disk list | GET, `bufaction=getDiskAllList` | See disk fields below | Source-confirmed |
| RAID array list | GET, `bufaction=getArrayList` | See RAID fields below | Source-confirmed |
| RAID maintenance settings | GET, `bufaction=getArrayMaintenance` | Maintenance configuration used by the UI | Source-confirmed, not a health endpoint |
| RAID miscellaneous settings | GET, `bufaction=getArrayMiscInfo` | `raidfail_shutdown`, `raidfail_boot_iscsi`, `shutdown_disable`, `raid_sync_speed` | Source-confirmed, configuration rather than current health |
| UPS settings/status | POST, `bufaction=getUPSSettings` | At least `syncUps`, `connectType`, `upsStatus`; optional feature-dependent fields | Source-confirmed |
| Sleep timer | POST, `bufaction=getSleeptimerSettings` | Schedule data consumed by the UI | Source-confirmed |
| Disk spin-down | POST, `bufaction=getHDDSpindownSettings` | Spin-down settings consumed by the UI | Source-confirmed |
| Active operation progress | POST, `bufaction=getOperationStatus` | Operation identifier and progress/detail values | Source-confirmed; not general health |

### Identity, features and system health

`getFeatures` is a generic list of feature keys and values. The root UI consumes
`SERIES_NAME`, `PRODUCT_NAME`, `workingmode`, `MAX_USBDISK_NUM`,
`SUPPORT_RAID`, `SUPPORT_SHUTDOWN_FROMWEB` and
`SUPPORT_REBOOT_FROMWEB`, among many other keys
(`static/root/root.js:136-219`). A feature is considered enabled when its value
is `1` or `on`; raw values remain available (`root.js:2765-2780`). These flags
govern UI visibility. They do not prove that a backend operation works on a
particular device.

The left panel requests `getRootSettings` with a numeric menu index appended to
the action name (`static/root/leftPanel.js:19-53`, `81-96`). Its first result
contains `generalInfo` name/value records. The renderer specially marks a record
named `system_warning` (`leftPanel.js:99-120`), so this is a candidate source for
system warnings. The available source does not define the complete name set,
severity, persistence, or whether multiple warnings are possible. It must be
captured before it can be mapped to Home Assistant system-error or information
entities.

The system-status reader expects `hostName`, `modelName`, `version`, date/time,
timezone, workgroup, NTP status, email-alert status and `fanStatus`
(`static/menus/status/system/status_system.js:118-141`). The UI maps numeric
`fanStatus == 1` to OK and every other value to error
(`status_system.js:188-192`). No CPU-health field or structured general
error/information list analogous to the modern portal response was found. The
root summary has the generic `system_warning` record described above, but its
semantics remain **unknown**.

There is an inconsistency in the collected status screen: its reader lists
separate time fields, while its formatter also accesses `data.time`
(`status_system.js:118-141`, `169-187`). A real response is needed before using
any of the time fields.

### Disks and storage

`getDiskAllList` records include:

- `disk`, `lvm`, `status`, `encryp`, `unitName`
- `capacity`, `amountUsed`, `percentUsed`, `fileFormat`
- `manufacturer`, `modelName`
- `canFormat`, `canRemove`, `canWakeup`, `over2tb`

Evidence: `static/menus/system/disk/diskMgmt_diskBasic.js:302-354`.

The UI displays `capacity` and `amountUsed` directly. Hardware confirms that the
backend includes comma-separated numbers and the `KB` suffix in these strings;
the UI does not calculate or append that suffix. Whether `KB` represents decimal
kilobytes or 1024-byte units remains unknown. `percentUsed` includes its percent
suffix in the backend value. Disk status strings are passed to the localization
table, with a special case for `normal_edp`; the absent locale data and lack of
failure captures still prevent a complete status vocabulary and safe abnormal
health mapping.

### RAID

`getArrayList` records include:

- `arrayName`, `arrayMode`, `arrayStatus`, `arrayFormat`
- `diskTotal`, `diskNormal`, `diskError`, `diskSpare`, `arrayDiskNum`
- `sizeUsedRaw`, `sizeUsedRate`, `sizeAllRaw`
- `timeStatus`, `rateStatus`, `editMode`, `canEDP`

Evidence: `static/menus/system/disk/diskMgmt_array.js:126-182`.

The UI explicitly appends KB to `sizeUsedRaw` and `sizeAllRaw`, percent to
`sizeUsedRate` and `rateStatus`, and minutes to `timeStatus`
(`diskMgmt_array.js:504-530`). The legacy adapter therefore preserves the raw
array values with their reported `KB` unit. Hardware confirms numeric strings,
but not whether each reported KB is 1000 or 1024 bytes, so conversion to bytes or
GiB remains intentionally unresolved. The basic disk `capacity` fields contain
the same numbers with the `KB` suffix on the captured array volume.

The generic bundle contains branches for `off`, RAID 1 and RAID 5 in array-edit
logic and references additional RAID modes elsewhere. This proves only that the
shared UI understands those identifiers, not that LS-WVL hardware exposes every
mode. Hardware confirms `arrayStatus=ok`, two entries in `diskNormal`, and a null
`diskError` for the healthy captured array. Neither the complete status
vocabulary nor degraded/failure samples are available. A conservative health
mapper should preserve unknown values and must not report them as healthy.

### UPS and power-management status

`getUPSSettings` is requested before rendering the UPS panel. The main screen
reads `syncUps`, `connectType` and `upsStatus`
(`static/menus/system/powerManagement/powerManagement_main.js:33-67`). The UPS
screen recognizes synchronization modes `off`, `master` and `slave`, and symbolic
status/connection identifiers such as `ups_status*` and `ups_connectType*`
(`powerManagement_upsSettings.js:919-1008`). Missing localization data prevents
reliable English health labels. Sleep-timer and HDD spin-down reads are settings,
not evidence of instantaneous system health.

## Shutdown and reboot

Both operations are state-changing and must never be used for capability
detection.

### Shutdown

- Endpoint: `/dynamic.pl`
- Method: POST through Ext form submission
- Body fields: `bufaction=shutRestartLS`, `action=shutdown`
- Authentication: an already authenticated Web UI session is inferred to be
  required; no SID field is included in this request
- Preparation: a user confirmation dialog; no preparatory API request is issued
  by the handler
- Success behavior: the page replaces its content with a shutting-down message
  and disables menus; it does not poll for completion
- Failure behavior: the common JSON envelope/error handler

Evidence: constants in
`static/menus/system/maintenance/system_maint_vars.js:22-30` and request handling
in `system_maint_shutdown.js:48-90`. The maintenance page validates the session
before constructing its panels (`system_maint_main.js:1-29`).

The shutdown source also contains a function that disables the button while
iSCSI is active (`system_maint_shutdown.js:94-98`), but no invocation was found
in the collected tree. Whether it is called by a missing or inline script is
**unknown**.

### Reboot

- Endpoint: `/dynamic.pl`
- Method: POST through Ext form submission
- Body fields: `bufaction=shutRestartLS`, `action=reboot`
- Authentication, preparation and errors: the same pattern as shutdown
- Success behavior: the UI displays a reboot message and disables menus; it does
  not poll for completion

Evidence: `static/menus/system/maintenance/system_maint_vars.js:22-30` and
`system_maint_restart.js:43-83`.

The root UI exposes these panels only when the corresponding feature flags are
enabled (`static/root/root.js:143`, `171`; `system_maint_main.js:219-235`). This
is **source-confirmed UI behavior**, not hardware confirmation. Shutdown and
reboot remain unverified on LS-WVL firmware 1.75.

## Difference from the LS220D/LS420D API

| Concern | LS-WVL 1.75 legacy UI | LS220D 1.86 / LS420D 1.88 integration |
|---|---|---|
| Endpoint | `/dynamic.pl` | `/nasapi/` |
| Protocol | CGI-style action dispatch | JSON-RPC 2.0 |
| Operation selector | `bufaction` form/query field | JSON-RPC `method` |
| Request identifier | None observed | JSON-RPC `id` |
| Authentication | Login form plus Web UI cookie | `Auth.login`, then SID in applicable parameters |
| Session check | Separate `validateSession` action | JSON-RPC authentication error and re-login handling |
| Response | JSON `success`/`errors`/`data` envelope | JSON-RPC `result` or `error` |
| Disk/RAID API | `getDiskAllList`, `getArrayList` | `device.getlist` plus modern portal data |
| Power operation | `shutRestartLS` plus an `action` field | `System.shutdown` / `System.reboot` |
| Hardware validation | Complete read-only capture; power operations not tested | LS220D 1.86 and LS420D 1.88 captures/operation results documented elsewhere |

Similar concepts do not imply identical field semantics. The legacy API returns
storage as comma-separated strings, sometimes with a `KB` suffix and sometimes
inside one-element arrays. The modern integration receives numeric KiB fields.
Legacy `ok` is hardware-confirmed for a healthy array, but abnormal legacy
health values do not yet have a verified mapping.

## Implemented integration architecture

LS-WVL support is implemented without extending the JSON-RPC transport with
legacy special cases. `BuffaloLinkStationApi` owns `/nasapi/`,
JSON-RPC request construction, SID injection and JSON-RPC errors. Those
assumptions are all different on LS-WVL.

The coordinator now consumes a small protocol-independent client contract with
two implementations:

1. The modern JSON-RPC client remains responsible for LS220D and LS420D.
2. The legacy CGI client owns `/dynamic.pl`, an isolated in-memory cookie jar,
   form/query encoding, legacy envelopes and fresh login after an explicit
   session rejection.
3. Both transports normalize into shared snapshot types for identity, disks,
   storage, RAID and health. Preserve raw vendor values alongside conservative
   normalized states so unknown statuses remain visible and unavailable rather
   than being mislabeled healthy.
4. Initial configuration selects the protocol with authentication and read-only
   identity requests, then stores it. Existing entries migrate explicitly to
   JSON-RPC without changing unique IDs, options or automations. Detection never
   calls shutdown or reboot.

The hardware capture defines the normalization boundary. The legacy response
adapter converts the feature ID/value list into a mapping, represents the
array volume separately from its physical member disks, and parses formatted
numbers only after stripping the exact captured separators and unit suffixes.
It retains both the parsed integer and the original unit label. It does
not feed these responses into the current modern `storage_totals()` or
`device.getlist` parsers, whose field names and unit contract are different.

A shared coordinator snapshot exposes protocol-neutral fields such as
identity, volumes, physical disks, arrays and optional system health. Fields not
returned by the legacy transport—currently firmware, CPU state, fan state,
system errors and system messages—remain `None`, so their entities are omitted
according to the existing entity lifecycle rather than populated with guesses.
The legacy array `ok` state is hardware-confirmed as healthy and normalized with
the raw value retained; all unknown states remain visible rather than being
silently labelled healthy.

Native username/password authentication is hardware-confirmed. The integration
validates a fresh session before reads and establishes a fresh confirmed login
after an explicit validation/session rejection. It does not imitate the Web
UI's timer-based cookie refresh or assume that doing so renews server state.
Read requests may be retried safely; state-changing requests are not retried
after an ambiguous transport failure. The manual-cookie path remains limited to
the standalone diagnostic.

Each config entry continues to own one client and one coordinator, preserving
multiple-NAS isolation. The existing config-entry identity, entity unique-ID
suffixes, power-control options, operation lock and entity registry entries
remain stable. Transport selection is stored as additional entry data rather
than changing existing IDs. Migration leaves modern entries on JSON-RPC and
does not alter their options or automations.

For legacy power controls, frontend feature flags may inform diagnostics but
must not automatically enable a destructive control. Hardware validation and an
explicit user setting are still required. State-changing calls must never be
retried after an ambiguous connection failure.

## Missing files and limitations

The following missing material is still important to complete the protocol
description:

- `/static/index.html` and its login form definition: useful for reconstructing
  the complete page and determining whether another login script is loaded;
  browser evidence has already confirmed the submitted credential fields.
- `static/menus/status/status_vars.js`: referenced by the status pages and needed
  to resolve the literal value of `BUFACT_GET_SYSTEM_STATUS` (and the network
  status action).
- Language/locale dictionaries used by `S(...)`: needed to map disk, RAID, UPS
  and error identifiers to meanings without guessing.
- A sanitized browser HAR of login and System Status: still useful to establish
  response content types, cookie response headers, required headers, session
  expiration behavior and the missing system-status action. The login request
  method and fields are already confirmed, while the completed capture
  establishes healthy field shapes and real feature values for the seven
  diagnostic reads.

Other unresolved references are not blockers for the core investigation:

- `status/statistics/status_statistics.js` could expose optional performance
  metrics, but it is not required for identity, disk, RAID or power requests.
- `system/disk/diskMgmt_volumes.js` and the missing volume/LVM variable files
  affect iSCSI/LVM management rather than the basic disk and array lists.
- Missing extension, user/group and ping variable files affect unrelated UI
  features.
- A misspelled WebAccess variable reference exists alongside a collected file
  with the expected spelling; it is unrelated to monitoring and power controls.

For completeness, the 13 unresolved script references found in
`static/root/root.js:294-923` are:

```text
static/menus/basic/security/basic_security_vars.js
static/menus/extensions/deviceserver/extensions_deviceserver_vars.js
static/menus/extensions/pocketu/extensions_pocketu_vars.js
static/menus/extensions/tmnas/extensions_tmnas_vars.js
static/menus/extensions/webaxs/extentions_webaxs_vars.js
static/menus/extensions/webservice/extensions_webservice_vars.js
static/menus/status/statistics/status_statistics.js
static/menus/status/status_vars.js
static/menus/system/disk/diskMgmt_volumes.js
static/menus/system/ping/system_ping_vars.js
static/menus/usrGrpMnt/usrGrp_vars.js
static/menus/volumes/lvm/volumes_lvm_vars.js
static/menus/volumes/volumes/volumes_vars.js
```

The WebAccess reference spells `extentions` differently from the collected
`extensions_webaxs_vars.js`, so that item may be a firmware loader typo rather
than a genuinely absent asset. The list records only unresolved references; it
does not imply that every file exists on an LS-WVL or is required for the
firmware paths investigated here.

The source collection contains no backend CGI implementation or API schema. The
sanitized hardware fixture now confirms the healthy response shapes documented
above, but it does not establish whether shutdown/reboot succeed or which
abnormal health values this device returns.

## Safe follow-up hardware-testing plan

The read-only diagnostic supports both native login and a manual-cookie
fallback, and its first cookie-based capture is complete. The missing
system-status action and session-expiration behavior still require a manual
browser-assisted evidence pass:

Before the first native-login capture, preserve the completed cookie-based file
under a distinct archival name inside the Git-ignored `reference/captures/`
directory. The tool will not overwrite or relocate it automatically.

1. Open the LS-WVL Web UI on an isolated trusted LAN and record a browser network
   capture while logging in and visiting only System Status, Disks/Array and UPS
   Status.
2. Do not open confirmation dialogs or submit any maintenance/configuration
   forms. Filter the capture to `/dynamic.pl`.
3. Record cookie names and attributes, response content type, expiration
   behavior, and the sanitized response structure for `verifyLogin`,
   `validateSession`, `getFeatures`, one representative
   `getRootSettings<menu index>` request, the system-status action,
   `getDiskAllList`, `getArrayList` and—if supported—`getUPSSettings`.
4. Remove passwords, SID/cookie values, usernames, serial numbers, MAC addresses,
   hostnames, IP addresses and other identifiers. Preserve raw numeric values,
   types, units implied by field names, symbolic statuses, missing fields and
   array/object structure. Keep the original capture outside the repository and
   never commit it.
5. Confirm the system-status `bufaction`, cookie response behavior and
   server-side expiration semantics before implementing unattended renewal.

The existing standalone diagnostic uses an `aiohttp.ClientSession` and a strict
allowlist containing only those seven read actions. It:

- accepts an interactive username and masked password for the confirmed login
  POST, or a temporary authenticated `webui_session_*` cookie through a masked
  fallback prompt; none are accepted through command-line arguments;
- logs method labels and sanitized error classes, never bodies, cookies or
  parameters containing credentials;
- executes each read independently after authentication, preserves successful
  results when another read fails, and marks the capture complete only when every
  required read succeeds;
- makes no automatic fallback to any `set*`, gate, disk-check, format, wake-up,
  shutdown or reboot action;
- preserves raw values without conversion in the sanitized local capture;
- saves only under the Git-ignored `reference/captures/` directory.

Native login is considered successful only when the CGI envelope reports
`success: true`, contains the required SID and page mode, and the following
`validateSession` call succeeds. The password, returned SID and cookies are not
added to the capture. The tool does not renew sessions or automatically log in
again after rejection because that behavior is not yet hardware-confirmed.

The first sanitized capture verified identity strings, empty and missing fields,
the reported `KB` label, RAID sizes and RAID1 mode, the healthy `ok` array value,
and use of the same temporary cookie across GET and POST reads. It did not test
session expiration. Degraded RAID, failed-disk and expired-session mappings
require naturally occurring or safely simulated evidence; they must not be
induced on production hardware.

## Remaining questions

- What literal `bufaction` returns system status?
- Does the login response set any server cookie in addition to the JavaScript
  cookie?
- Does the server require `X-Requested-With`, Referer or another header?
- What are the server-side session lifetime and invalid-session HTTP/envelope
  responses?
- Does the reported `KB` label represent decimal kilobytes or 1024-byte units?
- Which disk and array identifiers occur during degraded, rebuilding or failed
  states, and what is the localized meaning of `ups_status4`?
- Are CPU state, system errors and information messages available through an
  uncollected status/statistics action?
- Does the device expose a stable non-sensitive identifier suitable for config
  entry identity?
- Do shutdown and reboot work on LS-WVL hardware? The observed feature values
  are shutdown `0` and reboot `1`, but source presence and UI flags cannot answer
  whether either backend operation succeeds.
