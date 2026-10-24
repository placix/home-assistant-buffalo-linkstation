# Compatibility diagnostics for unsupported Buffalo models

This workflow helps investigate Buffalo LinkStation and CloudStation models
without publishing a browser's original network capture. It is intended for
unsupported devices such as LS441D, LS520D, LS720D, CloudStation CS-WV, and
other potentially compatible NAS models.

It does not add support automatically and does not prove that a model is safe
to control. Do not test shutdown, reboot, disk, RAID, firmware, user, or other
configuration-changing operations while collecting diagnostics.

## Current verified models

The integration currently has hardware evidence for:

| Model | Firmware | Verified interface |
| --- | --- | --- |
| LS220D | 1.86 | Modern JSON-RPC monitoring and shutdown |
| LS420D | 1.88 | Modern JSON-RPC monitoring and shutdown |
| LS-WVL | 1.75 | Legacy CGI login, session validation, and read-only monitoring |

LS-WVL shutdown and reboot are derived from firmware source but are not
hardware-verified. Models outside this table are not advertised as supported.
Buffalo product families can share a similar Web UI while using different
authentication rules, endpoints, parameters, response fields, or units.

## Important privacy warning

A HAR is a recording of browser network traffic. An original HAR can contain:

- a username and password submitted by the login form;
- active session cookies, session IDs, and authorization tokens;
- NAS addresses, private hostnames, serial numbers, and MAC addresses;
- email addresses, share names, filenames, and directory paths;
- browser, operating-system, and network metadata; and
- complete request and response bodies.

Anyone receiving an active token may be able to access the NAS with the
recorded session's permissions. **Never upload or attach the original HAR.**
If the NAS supports an appropriately restricted temporary test account, use
one and remove it after testing. Do not disable authentication, weaken NAS
security, expose the Web UI to the internet, or change security settings for
this workflow.

The sanitizer prioritizes removal over diagnostic completeness. It does not
claim to guarantee anonymity. You must inspect its output before sharing it.

## Record a focused HAR with Firefox

Perform this only from a trusted computer on the same trusted network as the
NAS.

1. Open the NAS Web UI in Firefox.
2. Press **F12** and select **Network**.
3. Enable **Persist Logs** so navigation does not clear the requests.
4. Clear the existing Network list to reduce unrelated browser traffic.
5. Start recording. Sign in normally, then visit only read-only pages such as
   System Status, Disk, RAID, Storage, or UPS Status.
6. Do not click shutdown, reboot, format, firmware-update, apply, save, user,
   permission, RAID-management, or other configuration controls.
7. Stop recording as soon as the read-only pages have loaded.
8. Right-click the Network list and select **Save All As HAR**.
9. Store the original HAR locally in an access-controlled location. Treat it
   like a password until it has been deleted.

Avoid unrelated websites, browser tabs, and extensions during the recording.
A short, focused capture reduces both privacy risk and review effort.

## Generate the diagnostic JSON

Run the sanitizer locally from the repository root. It uses the Python standard
library and does not require Home Assistant.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe tools\sanitize_har.py input.har `
  --model LS520D `
  --firmware 4.00
```

Linux or macOS:

```bash
python3 tools/sanitize_har.py input.har \
  --model LS520D \
  --firmware 4.00
```

By default, the tool writes `buffalo-diagnostic-sanitized.json` beside the
input HAR and refuses to overwrite an existing output. Choose another location
with `--output`:

```powershell
.\.venv\Scripts\python.exe tools\sanitize_har.py input.har `
  --output buffalo-diagnostic-sanitized-ls520d.json `
  --model LS520D `
  --firmware 4.00
```

The tool reads the local file only. It makes no NAS request, executes no API
operation, and uploads nothing. Original `.har` files and default generated
diagnostics are Git-ignored.

## What the output contains

The output is a new diagnostic document, not a modified HAR. It contains only
anonymous transaction ordering, HTTP method/status, a recognized endpoint
family, confirmed read-only API operation names, parameter names and types, and
a generalized response structure. Device-specific numeric measurements are
represented only by their data type, including storage sizes, percentages,
temperatures, counters, and values nested in arrays or objects. Tightly
allowlisted technical state strings and booleans may still be retained.

The sole numeric-response exception is the integer `error.code` inside a
validated JSON-RPC 2.0 error object returned for a confirmed Buffalo API method.
The envelope must contain a string error message and no result; the message is
still discarded. Arbitrary fields named `code`, malformed error-like objects,
CGI response fields, request parameters, HTTP body metadata, and telemetry never
receive this exception. HTTP status codes and counts calculated by the sanitizer
itself remain available because they are diagnostic structure rather than NAS
measurements.

Authentication responses and responses to power or configuration-like
operations are omitted and their names are replaced by generic operation
categories. Arbitrary JSON-RPC method names are never copied. Static resources,
ordinary Web UI navigation, unknown methods on known endpoints, and unrelated
requests are excluded from `transactions` and counted by broad category under
`summary.skipped_entries_by_category`.

When `/nasapi/` or `/dynamic.pl` is detected, unknown endpoint candidates are
summarized only as skipped entries. If neither known protocol is present, up to
five strongly reduced unknown candidates are retained. This allows investigation
of a genuinely different protocol without filling the output with images,
scripts, stylesheets, HTML, or arbitrary URLs.

Large `getNasFeature` results use a bounded feature-map profile: total field
count, value-type counts, and at most 32 safe technical field names. Individual
feature values and the full repetitive schema are not reproduced. Full URLs,
headers, cookies, timestamps, free-form strings, request IDs, and browser
metadata are never copied.

Legacy CGI `getFeatures` results are compacted separately. The diagnostic keeps
the feature-array length and a shared item schema when all entries have the same
shape. If shapes differ, it records the total schema-variant count and at most
eight deterministic variants with occurrence counts. Feature values remain
omitted. This optimization does not change array handling for other CGI actions.

### Confirmed operation names

The modern read-only allowlist is derived from the LS220D/LS420D Web UI
references and the integration implementation. It includes the portal, feature,
service, device, share, RAID, UPS, failover, backup, job, replication, SMB,
iSCSI-detail, network-list, folder-list, notification-detail, and user-list/detail
queries found there. Examples include `get_portal_info`,
`get_portal_info_authneed`, `getNasFeature`, `get_service_status`,
`device.getlist`, `share.getlist`, `raid.detail_raidmode_maintenance`, and
`ups.get_config`.

The legacy read-only allowlist remains the source- or hardware-confirmed set for
`/dynamic.pl`: `getFeatures`, `getDiskAllList`, `getArrayList`,
`getArrayMaintenance`, `getArrayMiscInfo`, `getUPSSettings`, and
`getOperationStatus`.

Confirmed authentication, logout, shutdown, reboot, edit, restore, and service
control methods are recognized only so their bodies can be minimized. Their
exact operation names are replaced by `<authentication-operation>` or
`<sensitive-operation>` in generated diagnostics. A syntactically plausible but
unconfirmed method is counted as `unrecognized_json_rpc_method` or
`unrecognized_legacy_cgi_action`; its captured name is discarded.

Example excerpt:

```json
{
  "format": "buffalo_compatibility_diagnostic",
  "format_version": 2,
  "protocol_evidence": [
    "modern_json_rpc"
  ],
  "privacy": {
    "automatic_anonymity_guaranteed": false,
    "review_required_before_sharing": true
  },
  "summary": {
    "detected_protocol_entries": {
      "modern_json_rpc": 27
    },
    "included_transaction_count": 8,
    "skipped_entries_by_category": {
      "static_resource": 300,
      "unknown_web_request": 150,
      "unrecognized_json_rpc_method": 19
    },
    "skipped_entry_count": 469
  },
  "transactions": [
    {
      "api_method": "device.getlist",
      "device": "<device-1>",
      "endpoint_path": "/nasapi/",
      "http_method": "POST",
      "http_status": 200,
      "protocol": "modern_json_rpc"
    }
  ]
}
```

The real output also lists which categories were removed or generalized.
Counts in this example are illustrative and are not device compatibility
claims.

## Review before sharing

Open the generated JSON in a text editor and inspect every line. Search for:

- the NAS username, password, IP address, and hostname;
- session IDs, cookies, tokens, and authorization strings;
- serial numbers and MAC addresses;
- personal names and email addresses;
- share names, filenames, and directory paths; and
- names of computers, domains, workgroups, or organizations.

Do not share the file if any such value remains. Delete the affected diagnostic
and report the sanitizer problem without quoting the value. You can also omit
the diagnostic entirely; credentials are never required for a compatibility
request.

## Submit a compatibility request

Open the repository's **Unsupported Buffalo model compatibility request** issue
form and provide:

1. the exact NAS model and firmware version;
2. the Home Assistant and integration versions;
3. the observed connection or setup problem;
4. read-only troubleshooting results; and
5. the manually reviewed sanitized diagnostic JSON, if you are comfortable
   sharing it.

Drag only the sanitized `.json` file into the issue. Never attach the original
HAR, screenshots containing identifiers, browser exports, cookies, passwords,
or raw network captures. Support is not guaranteed; the evidence first needs
technical and privacy review.

## Delete the original HAR

After the sanitized output has been reviewed and no further local investigation
is required:

1. close editors or browser tools that still have the HAR open;
2. delete the original HAR using the operating system's file manager;
3. empty the Recycle Bin or Trash if permitted by your retention policy; and
4. remove backup, cloud-sync, email, or temporary copies you knowingly created.

Reliable per-file overwriting is not guaranteed on SSDs, copy-on-write file
systems, cloud storage, or managed backups. The safest approach is to create and
keep the original only on encrypted, access-controlled storage, minimize its
lifetime, and follow the organization's approved data-destruction process.

## Known sanitizer limitations

- Automatic sanitization cannot prove anonymity or recognize every future
  vendor field.
- User-generated information used as an unusual JSON field name may be
  indistinguishable from a technical field name; manual review remains
  mandatory.
- Unknown, encrypted, compressed, malformed, oversized, or binary bodies are
  omitted rather than interpreted.
- Only JSON and form structures relevant to the recognized Buffalo endpoints
  are analyzed. Unknown protocols are deliberately reduced.
- Exact method/action names are emitted only from a reviewed allowlist backed by
  the available firmware references or current integration. A newly observed
  method is counted as unrecognized until its behavior and privacy impact are
  reviewed.
- Feature-flag diagnostics are intentionally truncated and may not show every
  supported feature name. Counts and value types remain available.
- Model and firmware values are supplied by the user and retained only when
  they match a restricted character set. Verify them before sharing.
- The output can reveal which API operations and data structures a device
  implements. Share it only in the intended compatibility issue.
