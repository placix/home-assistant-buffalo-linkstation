# v0.3.0 - Compatibility Diagnostics

## Highlights

- Added a local, privacy-first HAR diagnostic for investigating unsupported
  Buffalo LinkStation models.
- Added reviewed operation recognition for modern JSON-RPC and legacy CGI
  interfaces without publishing arbitrary method names.
- Added compact schema summaries for modern `getNasFeature` and legacy
  `getFeatures` responses.
- Stabilized legacy host-based device identities across address changes so
  existing entity unique IDs and customizations are preserved.
- Added regression tests, a guided GitHub compatibility-request form, and
  detailed diagnostic and manual-review documentation.

## Privacy

- Credentials, cookies, session identifiers, addresses, hostnames, serial
  numbers, paths, free-form strings, and device-specific numeric measurements
  are omitted or generalized.
- Authentication and state-changing operations are strictly minimized.
- Only validated JSON-RPC error codes use a narrowly restricted numeric-value
  exception.
- HAR processing is local-only and performs no uploads or network requests.
- Sanitized output still requires manual review before sharing.

## Verified hardware

- LS220D firmware 1.86: verified JSON-RPC monitoring and power controls.
- LS420D firmware 1.88: verified JSON-RPC monitoring and power controls.
- LS-WVL firmware 1.75: verified legacy CGI login, session validation, and
  read-only monitoring.

## Known limitations

- Diagnostics do not automatically enable support for another model.
- LS-WVL shutdown and reboot remain firmware-derived, disabled by default, and
  unverified on hardware.
- The byte basis of LS-WVL values labelled KB remains unknown, so they are
  exposed without conversion as `KB (reported)`.
- Degraded RAID and physical disk-failure conditions have not been captured on
  all verified hardware.
