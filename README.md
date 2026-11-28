# WattCycle Battery Utility

**Windows monitoring, logging, analytics, control, and automation for compatible WattCycle Bluetooth LiFePO4 batteries.**

> **Public beta — v0.3.0-beta.1.** This repository is derived from a hardware-tested personal prototype, but the cleaned discovery/configuration path still needs clean-system validation. Do not rely on it for unattended power control until you have tested it with your own equipment.

WattCycle Battery Utility is a **general-purpose battery application first**. It is intended for portable power, camping, RV/van systems, solar experiments, radio equipment, backup power, astrophotography, and other uses of compatible WattCycle/XDZN Bluetooth batteries.

ASCOM Alpaca / N.I.N.A. support is optional.

## Features

- Bluetooth LE battery discovery and remembered device selection
- Live SOC, pack voltage, signed current, power, remaining capacity and cycles
- Individual cell voltages, cell delta and temperature monitoring
- BMS warning/protection display
- 5-minute and 15-minute rolling load averages
- Runtime estimation based on observed load
- SQLite telemetry logging
- Automatic discharge-session detection, history and analytics
- Configurable low / critical / emergency thresholds
- Charge and Discharge MOS state reporting
- Optional MOS control — **disabled by default**
- Machine-readable state for automation/integrations
- Optional ASCOM Alpaca SafetyMonitor and Switch integration
- Stale/missing telemetry fails **Unsafe**

## Display modes

The public beta includes a modern CustomTkinter dashboard, dark/light themes, live telemetry charts, and two slim overlays. Select Horizontal monitor or Vertical monitor in the header. Single-click the overlay to restore the full dashboard; right-click for mode, opacity, always-on-top, metric selection, theme and exit. Drag to reposition; positions and preferences are remembered. Slim mode does not stop monitoring or logging. Stale readings are identified as last known. Test on Windows before relying on the new interface unattended.

The redesigned dashboard uses CustomTkinter. Existing installations must run `python -m pip install -r requirements.txt` before launching this build.

## Windows installer and updates

Download `WattCycle-Setup.exe` (recommended) or the portable `WattCycle.exe` from [GitHub Releases](https://github.com/SecondShiftLabs1/wattcycle-battery-utility/releases). Packaged builds do not require Python. The installer is currently unsigned. Settings and telemetry remain in their existing local data locations. In Settings, select Check for updates to download and verify a newer installer; installation requires confirmation and temporarily interrupts monitoring and ASCOM telemetry.

## First-run setup (source distribution)

1. Install Python 3.11+ on Windows.
2. Install dependencies with `py -m pip install -r requirements.txt` (or `python -m pip install -r requirements.txt` if your installation uses `python`).
3. Start `WattCycle_Battery_Utility.pyw`.
4. Select a compatible battery found by BLE discovery.
5. Configuration is stored locally in `%APPDATA%\WattCycleBatteryUtility\config.json`.

No personal battery MAC address, serial number, or machine-specific identifier is intentionally included in this repository.

## Safety

`allow_mos_control` defaults to `false`. With it disabled, MOS state may be monitored but the utility/integration will not intentionally write MOS state.

Enabling MOS control can remove power from connected equipment immediately. Test with non-critical loads first. If a computer, mount, storage device, network device, heater controller, or other equipment is powered by the battery, Discharge MOS OFF can cause an abrupt shutdown.

The Alpaca service is intended for trusted local networks. This beta does not provide authentication or TLS; do not expose it directly to the public Internet.

## ASCOM Alpaca / N.I.N.A.

The optional bridge exposes:

- **WattCycle Battery Safety Monitor** — Safe/Unsafe based on battery protection state and telemetry freshness.
- **WattCycle Battery + Rig Power** — battery telemetry as ASCOM Switch channels, with writable Discharge MOS only when explicitly enabled.

The intended automation pattern is: battery becomes unsafe → automation performs an orderly stop/park/warm-up → optional Discharge MOS control removes rig power as the final step.

## Compatibility

| Device family | Firmware | Prototype result |
| --- | --- | --- |
| WattCycle 12 V 100 Ah Bluetooth LiFePO4 | WT30_02504SW13_L_07 | Telemetry, status and Charge/Discharge MOS commands verified on physical hardware |

Compatibility with other WattCycle/XDZN models may vary. Hardware reports are welcome; please omit serial numbers unless they are needed for diagnosis.

## Protocol / related work

This project depends on **qume/wattcycle_ble**, an MIT-licensed Python library for WattCycle/XDZN BLE battery-management systems. Its documentation describes the BLE service, authentication, framing and telemetry protocol and states that the protocol was reverse-engineered from the WattCycle Android app.

- Upstream library: https://github.com/qume/wattcycle_ble
- Related ioBroker integration: https://github.com/ioBroker/ioBroker.wattcycle

Additional behavior used here was experimentally checked on physical WattCycle hardware, including current direction, MOS status bits and Charge/Discharge MOS control.

This project is not affiliated with or endorsed by WattCycle, ASCOM, N.I.N.A., qume, or ioBroker.

## AI-assisted development disclosure

This project began as a personal battery-monitoring and automation project and was developed interactively with OpenAI's ChatGPT.

A substantial portion of the application code, refactoring, documentation, debugging assistance and integration architecture has been AI-assisted or AI-generated. Human work includes defining requirements, operating and testing the physical hardware, capturing real device behavior, validating telemetry against the official application, testing BMS commands, evaluating safety behavior, and testing the N.I.N.A./ASCOM integration.

AI-generated code can contain defects. Review the source and validate safety-critical or power-control behavior on your own hardware before relying on it unattended.

## Status

**0.3.0-beta.1** — charging and discharge commands and telemetry have been tested on the listed hardware. The modern UI and slim modes are available for public testing. Clean-system installation, additional battery firmware compatibility, and unattended automation validation remain ongoing. This is a beta application, not a certified safety controller. Clean-system installer and update testing are ongoing.

## License

MIT. See `LICENSE`. Dependencies remain under their respective licenses.
