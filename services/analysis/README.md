# piFly analysis (Streamlit)

Interactive playground for recorded sessions: slice a window, write your own aggregation /
positioning function, plot the result (time series, XY, map, stats).

## Run

```bash
cd ~/Desktop/piFly
streamlit run services/analysis/app.py --server.port 8501 --server.headless true
# open http://localhost:8501
```

Sessions are read from `services/collector/logs/sessions` by default (editable in the sidebar;
`~/logs/sessions` on the Pi also works).

## The function contract

The code panel must define `process(x)` and return a dict. `x` is the selected window:

| key | contents |
|---|---|
| `t` | seconds from window start (float64, full rate) |
| `t_epoch` | absolute unix seconds |
| `ax, ay, az` | m/s² |
| `gx, gy, gz` | rad/s |
| `fs` | nominal sample rate |
| `gps` | `t, lat, lon, alt, sats, speed_kt, course` or `None` |
| `bme` | `t, temp_c, pressure_hpa, humidity_pct, cpu_temp_c` or `None` |
| `marks` | `t` + `label` list or `None` |
| `session`, `epoch0` | session id, window start (unix) |

Returned keys the app knows how to render:

| key | shape | rendered as |
|---|---|---|
| `path` | `{"t","lat","lon","alt"}` | map (line + start marker) |
| `position` | `{"t","east","north","up"}` | XY plot (+ map overlay, anchored at first GPS fix) |
| `velocity` | `{"t","vx","vy","vz"}` | speed + heading plot |
| `series` | `{name: (t, values)}` | time-series plot(s) |
| `stats` | `{name: number}` | stats table |
| `events` | `[(t, label)]` | result metadata (not plotted yet) |

Anything else is reported as "ignored".

## Helpers available inside the function

`bucket(t, v, dt, method="mean"|"median"|"min"|"max"|"std"|"rms"|"first"|"last"|"count")`,
`movavg`, `ema`, `lowpass`, `highpass`, `integrate`, `gps_to_enu`, `enu_to_gps`,
`pressure_to_alt`, `tilt_from_accel`, `complementary`, `mahony`, `remove_gravity`,
`body_to_world`, `zupt_mask`, plus `np`, `pd`, `signal`.

## Behaviour

- Windows are read straight from `imu.bin` with `np.memmap` → a 60 s slice of a 144M-record
  session loads in ~5 ms; "whole session" is decimated to the sample cap (stride is shown).
- Zero/garbage tail records (left by killed recordings) are skipped automatically.
- Your code runs in a separate process with a timeout, so hangs cannot freeze the app.
- Snippets live in `services/analysis/snippets/` (loaded/saved from the panel).
- "Pin" keeps the previous result as a dashed ghost to compare against, and the stats tab
  can export the current result to CSV.
