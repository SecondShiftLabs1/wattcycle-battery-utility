# Contributing

Bug reports and hardware compatibility reports are welcome.

For battery compatibility reports, please include:
- Battery model/capacity
- Firmware version
- Operating system
- Python version
- Whether discovery, telemetry and reconnect work
- Whether MOS status reporting works
- Whether MOS control was tested (only if you intentionally enabled it)
- Relevant error text

Please remove serial numbers, Bluetooth addresses, usernames and other identifiers from screenshots/logs unless they are necessary to diagnose the issue.

Power-control changes should default to the safest behavior. New writable controls must not silently become enabled for existing users.
