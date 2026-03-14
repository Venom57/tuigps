# Timing

The Timing tab provides detailed time and PPS (Pulse Per Second) information for precision timing applications.

![Timing](screenshots/timing.png)

## Panels

### Time & Timing
Detailed timing information including:

- **Date/Time** — Current GPS date and time in UTC
- **ept** — Estimated time error from the receiver
- **Leap** — Current leap seconds offset between GPS time and UTC

### PPS
Pulse Per Second timing data (requires PPS to be enabled on the Device tab):

- **Offset** — PPS offset between the GPS pulse and the system clock, displayed in nanoseconds, microseconds, or milliseconds depending on magnitude
- **Quality** — Color-coded quality indicator based on PPS offset (Excellent < 1us, Good < 10us, Fair < 100us, Poor > 100us)
- **Prec** — PPS precision (derived from the receiver's reported precision exponent)
- **qErr** — Quantization error reported by the receiver

If PPS is not enabled, a message directs you to the Device tab to enable it.

### TOFF
Time offset data computed from the GPS time in TPV messages vs the system clock captured at message receipt. Shows current offset, and accumulated statistics (mean, std dev, min, max) over the last 20 samples.

**Note:** The TOFF offset includes ~0.5-1s of serial latency (time for the receiver to serialize and transmit the NMEA/UBX data). This is expected and does not indicate clock error. For true clock accuracy measurement, use PPS.

### Armed Measurement
Press **Arm TOFF** to trigger a single-shot measurement: the system waits for the next TPV message, captures `time.time()` immediately when it arrives, and compares it with the GPS time from that message. The result shows:
- **Delta** — offset between GPS time and system time (includes serial latency)
- **GPS** — the GPS timestamp from the message
- **System** — the system timestamp at receipt

Press **Clear TOFF** to reset all TOFF history and armed results.

### TDOP
Time Dilution of Precision — a measure of how satellite geometry affects timing accuracy. Lower values indicate better timing geometry.

### Device
Shows the connected GPS device path, driver, baud rate, update cycle, and gpsd version.

## Enabling PPS
To see PPS data, navigate to the Device tab and use the Apply PPS button to configure the receiver's timepulse output. PPS data will then appear on this page automatically.
