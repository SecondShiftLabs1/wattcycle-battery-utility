# Changelog

## Unreleased

- Fixed first-run BLE discovery getting stuck when the upstream scan fails to return.
- Added an outer scan timeout, direct Bleak/Windows fallback, progress messages, and diagnostic errors.

## 0.1.0-alpha - 2026-10-01

- First repository-style public alpha derived from the working personal prototype.
- Removed hard-coded battery address.
- Added persistent JSON configuration in AppData.
- Added first-run BLE discovery/selection workflow.
- Made MOS writing opt-in and disabled by default.
- Preserved SQLite logging, discharge-session analytics, runtime estimation and alerts.
- Preserved optional ASCOM Alpaca SafetyMonitor and Switch integration.
- Added configurable stale-data fail-safe.
- Added project attribution, compatibility notes, safety documentation and AI-assisted-development disclosure.
