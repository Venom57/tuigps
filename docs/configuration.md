# Configuration

tuigps saves your settings in a small JSON file and reloads them at startup.
You never need to edit the file by hand: everything in it can be changed in
the app. It is documented here for scripting, troubleshooting and
provisioning.

## Where the file is

| Situation | Path |
|-----------|------|
| Default | `$XDG_CONFIG_HOME/tuigps/config.json` |
| `XDG_CONFIG_HOME` unset (or not an absolute path) | `~/.config/tuigps/config.json` |
| `tuigps --config PATH` | `PATH` (`~` is expanded); changes are saved back to it |

The file and its directory are created the first time a setting is saved.
`tuigps --help` prints the default path.

## When it is written

The file is saved when you:

- apply the Settings dialog (`s`)
- cycle the theme (`t`) or toggle dark/light (`d`)
- cycle the unit system (`u`)

Saving is atomic: the file is written to a temporary file in the same
directory, then renamed over the old one. A half-written config file never
replaces a good one. If saving fails (for example, the directory is
read-only), the app shows an error notification and keeps running with the
new settings for the current session.

## Keys

```json
{
  "host": null,
  "port": null,
  "units": "metric",
  "coord_format": "dd",
  "theme": "textual-dark",
  "log_format": "gpx",
  "log_dir": ".",
  "display_gnss": ["gps", "sbas", "galileo", "beidou", "imes", "qzss", "glonass", "navic"]
}
```

| Key | Type / values | Default | Meaning |
|-----|---------------|---------|---------|
| `host` | string or `null` | `null` | gpsd host name or address (IPv4, IPv6 or a name). It must not be empty or contain spaces. `null` means "not chosen": see [precedence](#server-precedence). |
| `port` | integer 1–65535 or `null` | `null` | gpsd TCP port. `null` means "not chosen". |
| `units` | `"metric"`, `"imperial"`, `"nautical"` | `"metric"` | Unit system (see [Units](#units)). |
| `coord_format` | `"dd"`, `"dms"`, `"ddm"` | `"dd"` | Coordinates as decimal degrees (`47.606200° N`), degrees/minutes/seconds (`47°36'22.32"N`) or degrees and decimal minutes (`47°36.37200'N`). |
| `theme` | Textual theme name | `"textual-dark"` | Color theme. An unknown name falls back to `textual-dark`, with a warning. |
| `log_format` | `"gpx"`, `"csv"` | `"gpx"` | Format of the next log started with `l`. |
| `log_dir` | non-empty string | `"."` | Directory for log files. `~` is expanded when a log starts, and the directory is created if missing. `"."` is the directory tuigps was started from. |
| `display_gnss` | list of constellation keys | all eight | Constellations shown in the sky plot, signal chart and satellite table (the [display filter](#constellation-display-filter)). Keys: `gps`, `sbas`, `galileo`, `beidou`, `imes`, `qzss`, `glonass`, `navic`. |

Unknown keys are ignored. Constellations are always saved in the order
above, whatever order the file lists them in.

### Units

| System | Distance, altitude, error estimates | Speed | Climb rate |
|--------|-------------------------------------|-------|------------|
| metric | m | km/h | m/s |
| imperial | ft | mph | ft/min |
| nautical | ft | knots | ft/min |

The unit system applies everywhere: position (altitudes, geoid separation),
velocity, error estimates (EPH/EPV/EPX/EPY/SEP as distances, EPS as a speed,
EPC as a climb rate) and position-hold statistics. Log files always use SI
units (metres, m/s).

### Constellation display filter

The display filter only changes what tuigps *shows*. Satellites of hidden
constellations are left out of the sky plot, the signal chart and the
satellite table, and each of these says how many satellites are hidden. The
Constellations panel still counts every constellation and marks hidden ones
as "(hidden)". The fix itself, the satellite counts in the Fix Quality panel
and in the status bar, and the receiver are not affected. To turn a
constellation on or off *in the receiver*, use the Device tab.

## Server precedence

The gpsd server is chosen in this order. The first match wins, separately
for the host and the port:

1. `--host` / `--port`
2. the positional server argument: `tuigps host`, `tuigps host:port`,
   `tuigps [ipv6]:port`, or `tuigps :port`
3. the `GPSD_HOST` / `GPSD_PORT` environment variables
4. `host` / `port` in the config file
5. `127.0.0.1` / `2947`

A server from the command line or the environment is used **for that
session only**: it is not written to the config file. The Settings dialog
opens with the server currently in use. A host or port is saved only when
you change it there; then it also takes effect at once (tuigps reconnects).

## Validation

**At startup**, loading never fails:

- If the file is missing, the defaults are used.
- If the file is unreadable, is not valid JSON or is not a JSON object, the
  defaults are used.
- Each invalid field (for example `"units": "furlongs"`, `"port": 70000`,
  an empty `display_gnss`) falls back to its default on its own. The valid
  fields are still used.
- Each problem is shown as a "Config: …" warning notification and logged.
  The file is not rewritten until you change a setting.
- Unknown constellation keys are ignored. If no known constellation is left,
  all are shown.

**In the Settings dialog**, every field is checked as you type, and **Apply**
stays disabled while any field is invalid. The error is shown under the
field.

| Field | Rule |
|-------|------|
| gpsd Host | not empty, no spaces |
| gpsd Port | an integer from 1 to 65535 |
| Log Directory | an existing writable directory, or a path that can be created (its nearest existing parent is a writable directory); `~` is allowed |
| Show constellations | at least one selected |

Enter in a text field applies the settings (when they are valid). Escape or
**Cancel** closes the dialog without changing anything.

**On the command line**, an invalid port (`--port 0`, `host:99999`,
`GPSD_PORT=abc`) or a malformed server (`[::1`) exits with a usage error
before the UI starts.
