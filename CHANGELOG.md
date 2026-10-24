# Changelog

## [0.3.0] - 2026-10-24

### Added

- Privacy-first, local HAR compatibility diagnostics for confirmed modern
  JSON-RPC and legacy CGI operations.
- A compatibility-request issue form and diagnostic review guide.

### Changed

- Legacy host-based device identities remain stable when connection details
  change, preserving existing entity unique IDs and registry customizations.
- Compatibility diagnostics omit device-specific numeric measurements and use
  bounded schemas for large feature responses.
- Hardware-validation wording now distinguishes tested power operations from
  comprehensive Home Assistant runtime validation.

## [0.2.0] - 2026-10-24

- Added hardware-verified LS-WVL login and read-only monitoring through its
  legacy CGI interface.
- Added safe protocol detection while preserving existing JSON-RPC entries.
- Added explicit, stable shutdown and reboot button availability controls.

## [0.1.1] - 2026-10-24

- Improved verified-model shutdown recognition and availability diagnostics.

## [0.1.0] - 2026-10-24

- Initial pre-release with LS220D and LS420D JSON-RPC monitoring and controls.

[0.3.0]: https://github.com/placix/home-assistant-buffalo-linkstation/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/placix/home-assistant-buffalo-linkstation/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/placix/home-assistant-buffalo-linkstation/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/placix/home-assistant-buffalo-linkstation/releases/tag/v0.1.0
