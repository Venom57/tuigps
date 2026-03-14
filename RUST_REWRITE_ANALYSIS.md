# Rust Rewrite Analysis for tuigps

## Executive Summary

tuigps is a ~2,500-line Python application with 18 source files that provides a real-time terminal UI for monitoring GPS data from gpsd. A Rust rewrite is very feasible — the Rust ecosystem has mature crates for every component. The main challenges are the TUI framework learning curve and gpsd protocol parsing (no mature gpsd client crate exists).

---

## Component-by-Component Mapping

### 1. TUI Framework: Textual → Ratatui

| Python (Textual) | Rust (Ratatui) |
|---|---|
| `App` class with screens/tabs | `App` struct with state enum for tabs |
| CSS-based layout (`app.tcss`) | Constraint-based layout (`Layout::default().constraints(...)`) |
| `Static` widgets with `render() → Text` | `Widget` trait with `fn render(self, area: Buf)` |
| `call_from_thread()` for thread→UI | `mpsc::channel` for thread→UI events |
| Built-in tabs, modals, scrolling | Manual tab state, popup rendering |
| Rich `Text` with markup | `ratatui::text::Line` / `Span` with `Style` |

**Crate:** [`ratatui`](https://crates.io/crates/ratatui) (v0.28+) with [`crossterm`](https://crates.io/crates/crossterm) backend.

**Effort:** Medium-High. Ratatui is immediate-mode (redraw every frame), unlike Textual's retained-mode widgets. You manage all state and rendering manually. Layout requires explicit `Rect` splitting instead of CSS grid. However, ratatui is very well-documented and has an active ecosystem.

**Alternative:** [`tui-realm`](https://crates.io/crates/tuirealm) adds a component model on top of ratatui (closer to Textual's widget pattern), but it's less widely used.

---

### 2. GPSD Client: python3-gps → Custom or gpsd-proto

| Python | Rust |
|---|---|
| `gps.gps()` session object | TCP socket + JSON parsing |
| `WATCH_ENABLE \| WATCH_JSON \| ...` flags | Send `?WATCH={"enable":true,"json":true,...}` |
| `session.read()` → dict | Read lines → `serde_json::from_str()` |
| `response["class"]` dispatch | `#[serde(tag = "class")]` enum dispatch |

**Crate options:**
- [`gpsd-proto`](https://crates.io/crates/gpsd-proto) — Basic gpsd JSON protocol types. Covers TPV, SKY, but may lack PPS/TOFF/DEVICE messages. Would need extension.
- **Custom implementation** (recommended) — gpsd's JSON protocol is simple line-delimited JSON. Define your own structs with serde and parse directly. This gives full control over TPV, SKY, PPS, TOFF, DEVICE, and VERSION messages.

**Effort:** Low-Medium. The gpsd JSON protocol is well-documented. The Python client is ~200 lines of extraction logic that maps directly to serde structs.

**Example approach:**
```rust
#[derive(Deserialize)]
#[serde(tag = "class")]
enum GpsdMessage {
    TPV(TpvData),
    SKY(SkyData),
    PPS(PpsData),
    TOFF(ToffData),
    DEVICE(DeviceData),
    VERSION(VersionData),
}
```

---

### 3. Data Model: dataclasses → Rust structs

| Python | Rust |
|---|---|
| `@dataclass` with `float("nan")` defaults | `struct` with `Option<f64>` fields |
| `math.isfinite()` checks | `Option::map()` / pattern matching |
| `IntEnum` for FixMode, FixStatus | `#[repr(u8)] enum` with `num_derive` |
| `List[SatelliteInfo]` | `Vec<SatelliteInfo>` |

**Key difference:** Python uses NaN sentinel values; Rust should use `Option<f64>`. This is more idiomatic and eliminates an entire class of bugs. Every `isfinite()` check becomes a natural `match` or `.map()`.

**Effort:** Low. Direct 1:1 mapping. The data model is ~150 lines of Python that become ~150 lines of Rust structs.

---

### 4. Threading: Python threading → Rust async or threads

| Python | Rust |
|---|---|
| `threading.Thread(daemon=True)` | `std::thread::spawn` or `tokio::spawn` |
| `threading.Lock()` | `Arc<Mutex<T>>` or channel-based |
| `app.call_from_thread(callback)` | `mpsc::Sender<Event>` |
| 2s reconnect delay | `std::thread::sleep` or `tokio::time::sleep` |

**Recommended approach:** Use `std::sync::mpsc` channels. The gpsd reader thread sends `Event::GpsUpdate(GpsData)` to the main loop. The main loop handles input events (crossterm) and GPS events in a single `select!`-style loop.

```rust
enum Event {
    Key(KeyEvent),
    Tick,
    GpsUpdate(GpsData),
    GpsError(String),
    NmeaLine(String),
}
```

**Effort:** Low. Rust's channel model is actually simpler than Python's `call_from_thread` pattern.

---

### 5. Widgets → Ratatui render functions

Each Python widget (12 total) becomes either a function or a struct implementing `Widget`:

| Widget | Complexity | Notes |
|---|---|---|
| PositionPanel | Low | Text formatting with `Option<f64>` |
| FixPanel | Low | Status display with colored spans |
| VelocityPanel | Low | Unit conversion + compass |
| TimePanel | Medium | PPS/TOFF stats display |
| ErrorPanel | Low | Simple ± value grid |
| DevicePanel | Low | Key-value display |
| ConnectionStatus | Medium | Multi-section status bar |
| SkyPlot | **High** | ASCII polar plot with trig |
| SignalChart | Medium | Horizontal bar chart |
| SatelliteTable | Medium | Sortable table with colors |
| ConstellationPanel | Low | Summary counts |
| NMEAViewer | Medium | Scrollable filtered log |

**SkyPlot** is the most complex widget — it does polar coordinate projection with aspect ratio correction onto a character grid. This translates directly to Rust but requires careful `Rect`/`Buffer` manipulation in ratatui.

**Effort:** Medium. Most widgets are straightforward Rich Text → ratatui Spans translation. The SkyPlot needs the most attention.

---

### 6. Formatting Utilities

| Python | Rust |
|---|---|
| `fmt(value, spec, suffix)` | `fn fmt_opt(value: Option<f64>, decimals: usize, suffix: &str) -> String` |
| `fmt_coord(value, axis, style)` | Same logic, `Option<f64>` input |
| `fmt_speed(mps, unit)` | Multiplication constants, same logic |
| `fmt_altitude(meters, unit)` | Same |

**Effort:** Low. Pure functions with simple math. ~100 lines.

---

### 7. Position Hold (Welford's Algorithm)

Direct port. Welford's online algorithm is ~40 lines in any language. The CEP calculation (CEP50 = 0.5887 × (σ_n + σ_e)) is trivial math.

**Effort:** Very Low.

---

### 8. GPS Logger (GPX/CSV)

| Python | Rust |
|---|---|
| `xml.etree.ElementTree` (GPX) | [`quick-xml`](https://crates.io/crates/quick-xml) or string formatting |
| CSV string formatting | [`csv`](https://crates.io/crates/csv) crate or manual |
| `pathlib.Path` / `open()` | `std::fs::File` / `BufWriter` |

**Effort:** Low. GPX is templated XML; CSV is trivial.

---

### 9. Clock Sync

| Python | Rust |
|---|---|
| `subprocess.run(["busctl", ...])` | `std::process::Command::new("busctl")` |
| `subprocess.run(["timedatectl", ...])` | Same pattern |
| `subprocess.run(["sudo", "-n", "date", ...])` | Same pattern |

**Effort:** Very Low. Direct `Command` equivalent.

---

### 10. u-blox Device Config

| Python | Rust |
|---|---|
| `subprocess.run(["ubxtool", ...])` | `std::process::Command::new("ubxtool")` |
| `struct.pack("<BBHhhIIIIiI", ...)` for UBX | `byteorder` crate or `u8` array with `to_le_bytes()` |
| `ctypes` for PPS ioctl | [`nix`](https://crates.io/crates/nix) crate for ioctl, or [`libc`](https://crates.io/crates/libc) |
| PPS kernel API (ioctl) | `nix::ioctl_readwrite!` macro |

**Effort:** Medium. The PPS ioctl interface requires unsafe Rust and careful struct layout. The ubxtool subprocess calls are trivial.

---

### 11. Settings Screen

A modal overlay in ratatui. Render a centered `Rect`, draw input fields and checkboxes manually. Use crossterm key events for navigation.

**Effort:** Medium. No built-in modal/form system in ratatui — you build it yourself.

---

## Dependency Summary

```toml
[dependencies]
ratatui = "0.28"
crossterm = "0.28"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
chrono = "0.4"              # Time/date handling
quick-xml = "0.36"          # GPX output
csv = "1"                   # CSV output
nix = { version = "0.29", features = ["ioctl"] }  # PPS kernel API
# gpsd-proto = "0.4"       # Optional, or roll your own
```

---

## Estimated Effort Breakdown

| Component | Python Lines | Rust Estimate | Effort |
|---|---|---|---|
| Data model + constants | ~300 | ~350 | Low |
| GPSD client + JSON parsing | ~350 | ~400 | Medium |
| Formatting utilities | ~120 | ~150 | Low |
| Main app (event loop, state) | ~350 | ~450 | Medium |
| Dashboard widgets (9 panels) | ~700 | ~900 | Medium |
| SkyPlot widget | ~150 | ~200 | High |
| SignalChart + SatTable | ~250 | ~300 | Medium |
| NMEA viewer | ~100 | ~120 | Low |
| Position hold | ~80 | ~80 | Very Low |
| GPS logger | ~100 | ~120 | Low |
| Clock sync | ~80 | ~60 | Very Low |
| Device config + PPS | ~200 | ~250 | Medium |
| Settings screen | ~100 | ~150 | Medium |
| **Total** | **~2,880** | **~3,530** | |

**Estimated total: ~3,500 lines of Rust** (excluding tests).

---

## Key Challenges

### 1. No CSS Layout System
Textual's CSS grid (`grid-size: 3 3; grid-rows: 3fr 4fr 3fr`) has no ratatui equivalent. You'll manually split `Rect` areas using `Layout::default().constraints([...])`. This is more verbose but gives full control.

### 2. Immediate-Mode Rendering
Ratatui redraws every frame. You need to manage when to redraw (on GPS update, on key press, on tick). Use an event loop:
```rust
loop {
    terminal.draw(|f| ui(f, &app))?;
    match rx.recv_timeout(Duration::from_millis(250))? {
        Event::Key(k) => handle_key(&mut app, k),
        Event::GpsUpdate(data) => app.gps_data = data,
        Event::Tick => {} // heartbeat refresh
    }
}
```

### 3. SkyPlot Polar Projection
The ASCII sky plot is the most complex rendering task. It needs:
- Trigonometric projection (elevation → radius, azimuth → angle)
- Character-level buffer manipulation
- Aspect ratio correction (terminal chars are ~2:1)
- Layered drawing (rings → crosshairs → satellites → labels)

This maps well to ratatui's `Buffer` API where you can set individual cells.

### 4. PPS Kernel Interface
The PPS ioctl requires `unsafe` Rust with correctly-laid-out C structs. Use the `nix` crate's ioctl macros. This is Linux-specific.

### 5. System python3-gps Eliminated
A major **advantage** of the Rust rewrite: no more `sys.path` hacking to find the system `python3-gps` package. The gpsd JSON protocol is parsed directly.

---

## Advantages of a Rust Rewrite

1. **Single binary** — No Python, no venv, no system package dependencies (except gpsd itself)
2. **Lower resource usage** — Textual+Rich is relatively heavy; ratatui is very lightweight
3. **No GIL** — True parallel gpsd reading and UI rendering
4. **Type safety** — `Option<f64>` instead of NaN sentinel values eliminates a class of bugs
5. **Startup time** — Near-instant vs Python import overhead
6. **Cross-compilation** — Easy to build for ARM (Raspberry Pi) targets with `cross`
7. **No sys.path hacks** — Direct JSON parsing replaces the fragile system package import

---

## Recommended Implementation Order

1. **Data model** — Define all structs, enums, constants
2. **GPSD client** — TCP connection, JSON parsing, reconnect logic
3. **Event loop** — Crossterm input + channel-based GPS events + tick timer
4. **Basic app shell** — Tabs, status bar, quit/key handling
5. **Simple widgets first** — FixPanel, VelocityPanel, ErrorPanel, DevicePanel
6. **PositionPanel + TimePanel** — More complex formatting
7. **SatelliteTable + ConstellationPanel** — Table rendering
8. **SignalChart** — Bar chart
9. **SkyPlot** — Polar projection (hardest widget)
10. **NMEA viewer** — Scrollable log
11. **GPS logger** — GPX/CSV output
12. **Position hold** — Welford's algorithm
13. **Settings modal** — Form overlay
14. **Clock sync** — Subprocess calls
15. **Device config** — ubxtool + PPS ioctl

---

## Conclusion

This is a well-scoped rewrite. The Python codebase is clean and modular, making it straightforward to port component-by-component. The Rust ecosystem covers every dependency. The main investment is in learning ratatui's layout/rendering model and implementing the sky plot. A developer familiar with Rust and TUI programming could complete this in **2-4 weeks** of focused work; someone learning Rust or ratatui along the way should budget **4-8 weeks**.
