# Device Configuration

The Device tab provides u-blox 8 receiver configuration via ubxtool.

![Device Configuration](screenshots/device.png)

## Controls

### Platform Model
Sets the receiver's dynamic platform model, which optimizes the navigation engine for the expected use case:

| Model | Use Case |
|-------|----------|
| Portable | General purpose (default) |
| Stationary | Fixed position (timing, base station) |
| Pedestrian | Walking speed |
| Automotive | Car/vehicle |
| Sea | Marine |
| Airborne <1g/2g/4g | Aviation with varying dynamics |

### Nav Rate
Sets the navigation solution update rate. Higher rates increase CPU and power usage:
- 1 Hz (default), 2 Hz, 5 Hz, 10 Hz

### Power Mode
Configures the receiver's power management:
- **Full Power** — Maximum performance, highest power consumption
- **Balanced** — Reduced power with minimal performance impact
- **Interval** — Periodic tracking
- **Aggressive 1Hz/2Hz** — Aggressive power saving with fixed update rate

### PPS Configuration
Configures the Pulse Per Second timing output:

- **PPS Frequency** — Output pulse rate (1 Hz to 10 kHz)
- **PPS Duration** — Pulse width (10 us to 100 ms)
- **Apply PPS** — Sends the selected frequency and duration to the receiver
- **Disable PPS** — Turns off the timepulse output
- **Read PPS** — Reads current CFG-TP5 configuration from the receiver

### Constellations
Toggle buttons to enable/disable GNSS constellations on the receiver. Green indicates enabled. Available constellations: GPS, GLONASS, GALILEO, BEIDOU, SBAS, QZSS.

### Utility Buttons
- **Save Config** — Save current configuration to receiver flash (persists across power cycles)
- **Cold Boot** — Force a cold start (clears ephemeris, almanac, and position)
- **Read Nav** — Read current CFG-NAV5 navigation configuration
- **Read Rate** — Read current CFG-RATE update rate configuration
- **Read GNSS** — Read current CFG-GNSS constellation configuration

### Raw Command Input
Enter any ubxtool command arguments directly (e.g., `-p MON-VER` to read firmware version). Output appears in the log area below.

## Command Output
The bottom panel displays the output from all ubxtool commands, including responses from the receiver and any error messages.
