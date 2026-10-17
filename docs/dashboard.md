# Dashboard

The Dashboard (`1`) is the main view: everything important about the fix on
one screen.

![Dashboard](screenshots/dashboard.png)

*Screenshot from a real receiver. The status bar's key hints have changed
since it was taken: they now include Log, Hold and `?` Help, as space
allows.*

## Layout

The panel grid adapts to the terminal width:

| Width | Layout |
|-------|--------|
| ≥ 120 columns | 3 columns: Position, Fix, Velocity / Sky plot (2 wide), Signal / Errors, Device, Time |
| 80–119 columns | 2 columns: Position, Fix / Velocity, Errors / Sky plot, Signal / Device, Time |
| < 80 columns | 1 column, all panels stacked |

Text panels are as tall as their content. The sky plot takes the remaining
height, down to a minimum. If the terminal is too short, the dashboard
scrolls instead of cutting panels off.

## Panels

All distances, altitudes, speeds and error estimates follow the unit system
(`u` or Settings): metric, imperial or nautical. Coordinates follow the
coordinate format (DD, DMS or DDM). Values the receiver did not report are
shown as `---`.

### Position
Latitude and longitude, altitude above the ellipsoid (HAE) and above mean
sea level (MSL), and the geoid separation. With a fix, there is also a
clickable **Open in Google Maps** link (`m` does the same).

While position hold is running (`h`), a **Hold** section shows the fix count
and elapsed time, the mean latitude, longitude and altitude, and the
standard deviation N/S, E/W and in altitude. It also shows CEP50 and CEP95,
colored green/yellow/red by size. Standard deviations and CEP need at least
two fixes. Altitude statistics need fixes that report an altitude.

### Fix Quality
Fix mode (No Fix, 2D Fix, 3D Fix) and status (GPS, DGPS, RTK Fix/Float, DR,
Time Only, PPS Fix and so on), satellites used and visible, and HDOP, VDOP,
PDOP, GDOP and TDOP, each rated: Ideal < 1, Excellent < 2, Good < 5,
Moderate < 10, Fair < 20, Poor otherwise.

### Velocity
Speed, track (true heading, with a compass point such as `NE`), magnetic
track, climb rate (m/s, or ft/min in imperial and nautical) and magnetic
variation.

### Sky plot
An ASCII polar plot. The center `+` is the zenith, and dotted rings mark the
horizon and 30° and 60° elevation, with N/S/E/W labels. A
satellite is drawn as `#` when used in the fix and `o` otherwise, followed
by its satellite number and colored by constellation. Satellites below the
horizon or without a position are not drawn. Constellations hidden by the
display filter (Settings) are not drawn either.

### Signal Strength
One bar per satellite with a signal (SNR in dBHz, scaled to 55). Satellites
used in the fix come first, marked `•` and drawn in their constellation's
color. Unused satellites have grey bars. When the panel is full, a
`... +N more` line counts the rest. The legend lists the colors of the
constellations that are shown, and a line counts the satellites hidden by
the display filter.

### Error Estimates
gpsd's error estimates, shown as ±values: EPH (horizontal), EPV (vertical),
EPT (time), EPS (speed), EPD (track, degrees) and EPC (climb). EPX, EPY
(longitude/latitude) and SEP (spherical) appear only when the receiver
reports them.

### Device
The receiver's device path, driver, baud rate, update cycle and the gpsd
version. When gpsd reports no device, it says "No device" and shows the gpsd
version and protocol, if known.

### Time & Timing
GPS date and time (UTC), the time error estimate (ept, in µs, ms or s) and
the leap-second count. The Timing tab has the full timing view.

## Stale and disconnected data

When the data on screen is not live, every data panel on the Dashboard,
Satellites and Timing tabs is dimmed and its border is tagged:

| Tag | When |
|-----|------|
| `[CONNECTING]` | tuigps is connecting to gpsd |
| `[DISCONNECTED]` | the connection failed or was lost (tuigps keeps retrying) |
| `[STALE 12s]` | connected, but no gpsd message for more than 10 seconds (the count goes up) |

The last known values stay visible but dimmed, so they can't be mistaken for
live data. The tag clears as soon as fresh data arrives.

## Status bar

The bottom bar has key hints on the left. In the middle are activity badges:
`REC n` while logging, `HOLD n` while averaging. The GPS status is on the
right.

- **Connected, live:** satellites used per constellation and the number
  visible (for example `4GP+2GA/11sv`; `0/11sv` while none are used), the
  device path, then the fix mode and status (for example `3D Fix  DGPS`).
- **Problems:** `CONNECTING…`, `DISCONNECTED — retry in 8s`, `WAITING`
  (connected, no report yet) or `STALE (12s)`.

The status always fits. When the terminal is narrow, the bar drops, in
order: the device path, the satellite counts, the badge counts and the key
hints that don't fit. Dropped hints are replaced by `? Help`.

Connection problems are also announced once per outage with a notification,
and a reconnect is announced with "Connected to gpsd host:port". While gpsd
is down, tuigps retries with backoff from 2 s up to 30 s. `r` reconnects
immediately.
