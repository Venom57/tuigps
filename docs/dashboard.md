# Dashboard

The Dashboard is the main view of tuigps, providing a comprehensive overview of GPS status in a single screen.

![Dashboard](screenshots/dashboard.png)

## Panels

### Position
Displays current latitude, longitude, altitude (HAE and MSL), geoid separation, and a clickable Google Maps link when a fix is available. When position hold is active, shows averaged position, standard deviation (N/S, E/W, altitude), and CEP50/CEP95 statistics.

### Fix Quality
Shows fix mode (No Fix / 2D / 3D), fix status (GPS, DGPS, RTK, PPS, etc.), satellites used vs visible, and DOP values (HDOP, VDOP, PDOP).

### Velocity
Shows speed (in the selected unit system), track/heading, climb rate, and magnetic variation when available.

### Sky Plot
ASCII polar plot showing satellite positions by elevation and azimuth. Satellites in use are highlighted. Each satellite is labeled by SVN number and color-coded by constellation.

### Signal Strength
Bar chart of satellite signal-to-noise ratios (SNR in dBHz), color-coded by constellation (GPS, GLONASS, Galileo, BeiDou, etc.).

### Error Estimates
Displays estimated position errors: horizontal (eph), vertical (epv), speed (eps), track (epd), climb (epc), and spherical (sep).

### Device
Shows the connected GPS device path, driver, baud rate, and update cycle.

### Time & Timing
Displays current GPS date and time in UTC, time error estimate (ept), and leap seconds.

## Status Bar

The bottom status bar shows key bindings on the left, activity badges in the middle (REC when logging, HOLD when averaging), and GPS status on the right including constellation breakdown (e.g., "4GP+2GA/11sv"), device path, fix mode, and status.

## Key Bindings

All key bindings are shown in the status bar at the bottom of the screen. See the main [README](../README.md) for the full list.
