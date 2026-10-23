# LS220D 1.86 and LS420D 1.88 firmware comparison

## Evidence and confidence labels

This comparison is based primarily on the locally retained Buffalo Web UI
JavaScript. It describes what each browser client sends and consumes; matching
JavaScript does not prove that the two NAS backends accept every method or
return identical values.

- **Source-confirmed**: directly used by one or both supplied JavaScript trees.
- **Hardware-confirmed**: reported as successfully exercised on the named NAS.
- **Inferred**: strongly suggested by the frontend but not demonstrated by an
  actual response or operation.
- **Unknown**: not established by the available source or hardware evidence.

No vendor source is reproduced here. Method names, parameter names, field
names, and independently described behavior are recorded as interoperability
facts.

## Bundle comparison

| Area | LS220D firmware 1.86 | LS420D firmware 1.88 | Confidence |
| --- | --- | --- | --- |
| Collected files | 43 | 41 | Source-confirmed |
| Files common to both bundles | 41; 35 are byte-identical | 41; 35 are byte-identical | Source-confirmed |
| Model-only files | `ag-scripts.js`, Japanese Ext locale | None in this collection | Source-confirmed collection difference |
| JSON-RPC endpoint and envelope | POST `/nasapi/`; JSON-RPC 2.0 object | Same implementation | Source-confirmed |
| Default common-request timeout | 45 seconds | 20 seconds | Source-confirmed frontend behavior |
| Login and SID handling | Same semantic implementation | Same semantic implementation | Source-confirmed |
| Invalid-session code | `-119` | `-119` | Source-confirmed |
| Portal/system fields | Same field consumers | Same field consumers | Source-confirmed frontend expectation |
| Device/RAID enumeration | `device.getlist`; same parsing | Same implementation except zero-progress handling | Source-confirmed |
| Portal capacity conversion | Divide by 1024 twice; total is truncated to one decimal for display | Divide by 1024 twice; total is rounded/formatted to one decimal | Source-confirmed display difference |
| Username lookup | `user_.get_username` | `User.get_username` | Source-confirmed model difference |
| Long-running job-detail timeout | 10 seconds | 60 seconds | Source-confirmed frontend behavior |
| Shutdown call | `System.shutdown`, no operation-specific parameters | Byte-identical implementation | Source-confirmed; also hardware-confirmed on both |
| Reboot call | Not present in collected files | Not present in collected files | Unknown from these bundles |

Other differences in the shared files concern browser detection, input
validation, online-help behavior, and an external connectivity-check URL. They
do not change the NAS monitoring protocol used by this integration.

## Common JSON-RPC and authentication behavior

Both `utils.js` implementations construct the same request envelope with
`jsonrpc`, `method`, `params`, and a string request `id`. Requests go to
`/nasapi/` using HTTP POST and JSON content. When `params` is absent, the Web UI
creates an object. It injects the `sid` cookie unless missing static API
metadata marks the method as not using a SID.

Both firmware finalizers call `getNasFeature` with no explicit parameters
before the role and authenticated portal steps. The static `api.json` file that
supplies `isNotUseSID` is absent from both retained reference trees, so that
flag cannot be inspected directly. However, a working browser request was
observed with `params: {}`, and real LS220D 1.86 and LS420D 1.88 requests that
included `params.sid` failed with `-32602` and `-32603`, respectively. The
method is therefore hardware-confirmed as session-independent on both models;
the integration sends an empty parameter object and does not authenticate for
this call.

`Auth.login` receives `username` and `password`. The Web UI adds `timeout: "0"`
only when its persistent-session option is selected. On success it stores the
returned `sid` and `uid`; the integration intentionally retains these only in
memory and does not request an unlimited browser session.

Both clients treat error `-119` as an invalid session, display login, and retry
the rejected request after successful authentication. The integration follows
the same code-specific behavior without matching translated error text.
Authentication failures from `Auth.login` are also surfaced as authentication
errors. A read can be retried after `-119`; a power request is never retried
after a connection failure or timeout because acceptance is then ambiguous.

The Web UI considers a response successful when it contains `result`, including
`null`, and otherwise processes `error`. It does not validate the response ID
or protocol version. The integration performs those additional JSON-RPC
integrity checks; actual hardware responses that omit either field have not
been observed, so that stricter behavior remains an assumption to validate.

## Observed errors

| Code | Meaning in the JavaScript | Integration behavior | Confidence |
| --- | --- | --- | --- |
| `-119` | Invalid/expired SID; prompt for login and retry | Reauthenticate once and resubmit the explicitly rejected call | Source-confirmed |
| `-130` | Enters the Web UI database-recovery path | Ordinary JSON-RPC error | Source-confirmed UI behavior; backend meaning unknown |
| `-32603` | Also enters the database-recovery path | Ordinary JSON-RPC error | Source-confirmed UI behavior; backend cause unknown |
| `-29970` | Web UI could not parse response text | Integration invalid-response exception | Source-confirmed client-local code |
| `-29960` | Web UI found neither result nor error | Integration invalid-response exception | Source-confirmed client-local code |
| `-29990` | Web UI request timeout | Integration timeout exception | Source-confirmed client-local code |
| `-29980` | Other Web UI transport failure | Integration connection exception | Source-confirmed client-local code |

The complete NAS error catalog is unavailable because the static API/error
metadata loaded by the Web UI is not part of either reference collection.

## System, storage, and RAID data

Both header implementations consume these portal fields from
`get_portal_info` or `get_portal_info_authneed`: `hostname`, `product_name`,
`fw_version_major`, `fw_version_minor`, `serial_number`, `cpu_status`,
`errors`, `informations`, `interfaces`, `auth_method`, `workgroup`, and `disks`.
The authenticated method is used after a role is established.

Portal disk entries are consumed with `device_name`, `usage`, `used_size`,
`total_size`, and `used_percentage`. Both clients divide the two size values by
1024 twice and label the result as GB. The integration interprets the source
unit as KiB and exposes GiB. This unit interpretation is source-confirmed by the
conversion and hardware-validated against sanitized values from both models.
The LS220D/LS420D rounding difference is presentation-only, so the integration
does not add model-specific capacity parsing.

`device.getlist` returns an object containing `devices`. Both clients consume
`device_id`, `device_name`, `device_type`, `status`, `usage`, `interface`,
`slaves_device_id`, `holders_device_id`, and `progress`. An array/RAID object
uses `device_type: "array"`; partition, disk, and LVM records are also handled.
The common severity order is: broken, rebuild, degrade, reshape, resync, repair,
removed, formatting, checking, disconnected, unformatted, then fine.

The LS420D client avoids replacing a device's progress object when the computed
aggregate percentage is zero; LS220D does replace it. This does not affect the
integration because it does not expose job progress. Hardware captures confirm
six device records on each model: two SATA disks, two SATA partitions, one
array, and one disconnected USB placeholder. LS220D reports RAID1 while LS420D
reports RAID0. The integration excludes the USB placeholder from internal-disk
health and evaluates array health separately.

## Known methods and parameters

The following table covers the integration-relevant calls plus the additional
active calls found across the trees. The common request layer may add `sid`.

| Method | Explicit parameters observed | Notes |
| --- | --- | --- |
| `Auth.login` | `username`, `password`; optional `timeout` | Login response supplies `sid` and `uid` |
| `Auth.get_uid` / `auth.get_uid` | None | Case varies between call sites |
| `Auth.logout` | None | Clears browser-side login state afterward |
| `User.detail` | `uid` | Used to obtain roles |
| `user_.get_username` | `uid` | LS220D spelling |
| `User.get_username` | `uid` | LS420D spelling |
| `get_portal_info` | None | Unauthenticated/public portal variant in the UI |
| `get_portal_info_authneed` | None | Authenticated portal variant used by the integration |
| `getNasFeature` | Empty object; no SID | Session-independent on both verified models |
| `device.getlist` | None | Result contains `devices` |
| `get_service_status` | `service_name` | Result is a bit field under `service_status[service_name]`; bit 0 is enabled |
| `service_control` | `service_name`, `command` | `command` is enable or disable; integration does not call it |
| `share.getlist` | None | Share list; not exposed as HA state |
| `list_folders` | `path` | Folder browser |
| `Job.detail` | `jobid` | Long-running job polling |
| `Job.listJobs` | None | Active-job discovery |
| `Backup.listSources` / `Backup.listTargets` | Caller-supplied selection options | Exact complete schema unknown |
| `Backup.updateFolders` | Optional `clear_ls_list` and caller data | Mutating; integration does not call it |
| `network.listinterfacesettings` | None | Returns interface records |
| `user.getlist` | `category` | Categories include local, domain, and external |
| `System.shutdown` | None beyond common SID | Administrator role checked by Web UI |

Other source references include failover, replication, SMB, UPS, mail,
maintenance, iSCSI, sound-location, and restore methods. Several detail/edit
calls exist only in commented legacy branches, so their current backend support
and full parameter schemas are unknown.

## Feature flags

The same 21 feature-flag names occur in both collections:
`SUPPORT_AV`, `SUPPORT_DTCP_IP`, `SUPPORT_EYEFI`, `SUPPORT_FLICKRFS`,
`SUPPORT_FLICKRS`, `SUPPORT_FOFB`, `SUPPORT_IMHERE`,
`SUPPORT_LAYOUT_SWITCH`, `SUPPORT_NUT`, `SUPPORT_OFFLINEFILE`,
`SUPPORT_PRINTER_SERVER`, `SUPPORT_RAID`, `SUPPORT_REBOOT_FROMWEB`,
`SUPPORT_REPLICATION`, `SUPPORT_SHUTDOWN_DEPEND_ON_SW`,
`SUPPORT_SHUTDOWN_FROMWEB`, `SUPPORT_SXUPTP`, `SUPPORT_TERA_SEARCH`,
`SUPPORT_TIME_MACHINE`, `SUPPORT_UPS_USB`, and `SUPPORT_UTILITY_DOWNLOAD`.

Occurrence only proves that the frontend understands a flag. It does not prove
that a particular model returns it as enabled or that the underlying API method
is available. Both completed hardware captures report
`SUPPORT_SHUTDOWN_FROMWEB = 0` even though shutdown works on both devices.

## Power operations

`System.shutdown` is source-confirmed in byte-identical shutdown-page code for
both bundles. It is also hardware-confirmed on LS220D firmware 1.86 and LS420D
firmware 1.88. A missing shutdown option defaults to enabled for those exact
pairs and disabled for other pairs; an explicit option always controls button
availability. `SUPPORT_SHUTDOWN_FROMWEB` controls browser UI visibility and is
not treated as an authoritative backend capability flag.

No `System.reboot` string occurs anywhere in either collected tree. The
previously observed reboot-page call using `System.reboot` is credible evidence
from a page that may simply be absent from these collections, but it is not
confirmed by the files currently retained. The method has since been exercised
through Home Assistant on real hardware. It remains disabled by default,
controlled by an explicit option, and is never probed automatically.

## Remaining uncertainties

- Only healthy portal and `device.getlist` states have been captured. Degraded,
  rebuilding, failed-disk, and non-empty message/error shapes remain unverified
  on hardware.
- Static API metadata is missing, including the complete method catalog,
  most per-method timeout declarations, other SID exemptions, and error
  descriptions. The `getNasFeature` SID exemption is independently confirmed
  by the browser request and hardware behavior.
- The backend's treatment of JSON-RPC response IDs/protocol markers needs
  hardware confirmation because the Web UI does not validate them.
- The reboot page and its role checks remain absent from the retained source.
- Feature-flag values other than the reported LS220D shutdown value have not
  been captured for either model.
