# Security and safety

This application can optionally control battery MOS outputs. A software defect or incorrect configuration could interrupt power to connected equipment.

MOS writing is disabled by default. Do not enable it until you understand which devices are powered by the battery and have tested the behavior with non-critical loads.

The Alpaca service is intended for trusted local networks. This alpha does not provide authentication or TLS. Do not expose its TCP port directly to the public Internet.
