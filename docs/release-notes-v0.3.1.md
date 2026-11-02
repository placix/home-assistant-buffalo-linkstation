# v0.3.1 - LS-WVL Power Validation

## Changes

- Documented successful shutdown and reboot testing through Home Assistant on
  real LS-WVL firmware 1.75 hardware.
- Confirmed the existing legacy CGI requests use the hardware-tested
  `shutRestartLS` action with `shutdown` or `reboot`.
- Kept both LS-WVL power controls disabled by default and strictly
  user-initiated.
- Clarified the HACS installation path while the default-repository submission
  is under review and after it has been accepted.

## Verified hardware

- LS220D firmware 1.86: JSON-RPC monitoring and power controls.
- LS420D firmware 1.88: JSON-RPC monitoring and power controls.
- LS-WVL firmware 1.75: legacy CGI login, read-only monitoring, shutdown, and
  reboot.

No automatic power-operation detection or background power request was added.
