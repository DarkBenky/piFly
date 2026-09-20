import json
import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    from code_editor import code_editor
    HAS_EDITOR = True
except Exception:
    HAS_EDITOR = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import executor
import loader

COLORS = ["#6cf", "#e8a33d", "#5fd6a8", "#b98ee8", "#ff8f6b", "#7fd1ff", "#e8e36b", "#ff7b9c"]
TEMPLATE = open(os.path.join(loader.SNIPPETS_DIR, "default.py")).read()

st.set_page_config(page_title="piFly Analysis", page_icon="🛰️", layout="wide")
loader.ensure_dirs()


def envelope(t, v, buckets=1500):
    if len(t) == 0:
        return np.array([]), np.array([]), np.array([])
    edges = np.linspace(0, len(v), min(buckets, len(v)) + 1).astype(int)
    edges = np.unique(edges)
    lo, hi, mid, tm = [], [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        chunk = v[a:b]
        if len(chunk) == 0:
            continue
        lo.append(np.nanmin(chunk))
        hi.append(np.nanmax(chunk))
        mid.append(np.nanmean(chunk))
        tm.append(t[a:b].mean())
    return np.array(tm), np.array(lo), np.array(hi), np.array(mid)


@st.cache_data(show_spinner=False)
def cached_jsonl(path, stream, t0, t1, max_records=300_000):
    return loader.read_jsonl(path, stream, t0, t1, max_records=max_records)


@st.cache_data(show_spinner=False)
def gps_coverage(session_path):
    rows = loader.read_jsonl(session_path, "gps", None, None, max_records=2_000_000)
    times = [row["t"] for row in rows if row.get("t") is not None]
    if not times:
        return None
    return {"count": len(times), "t0": min(times), "t1": max(times)}


def padded_frame(columns):
    length = max((len(value) for value in columns.values() if value is not None), default=0)
    data = {}
    for name, value in columns.items():
        if value is None:
            continue
        array = np.asarray(value, dtype="f8")
        if len(array) < length:
            array = np.pad(array, (0, length - len(array)), constant_values=np.nan)
        data[name] = array
    return pd.DataFrame(data)


def signal_figure(window, marks, keys=("ax", "ay", "az", "gx", "gy", "gz")):
    fig = go.Figure()
    for index, key in enumerate(keys):
        t, lo, hi, mid = envelope(window["t"], window[key])
        fig.add_trace(go.Scatter(x=t, y=hi, line=dict(width=0), showlegend=False, hoverinfo="skip",
                                 fillcolor=COLORS[index % len(COLORS)], opacity=0.18, name=f"{key} range"))
        fig.add_trace(go.Scatter(x=t, y=lo, line=dict(width=0), showlegend=False, hoverinfo="skip",
                                 fillcolor=COLORS[index % len(COLORS)], opacity=0.18, fill="tonexty"))
        fig.add_trace(go.Scatter(x=t, y=mid, name=key, line=dict(color=COLORS[index % len(COLORS)], width=1.4)))
    if marks:
        for mark_t, label in zip(marks["t"], marks["label"]):
            fig.add_vline(x=float(mark_t), line=dict(color="#555", width=1, dash="dot"),
                          annotation_text=label, annotation_font_size=9)
    fig.update_layout(template="plotly_dark", height=340, margin=dict(l=10, r=10, t=30, b=10),
                      paper_bgcolor="#1a1a1a", plot_bgcolor="#1a1a1a", hovermode="x unified",
                      xaxis=dict(title="window time (s)", gridcolor="#2a2a2a"),
                      yaxis=dict(gridcolor="#2a2a2a"), legend=dict(orientation="h", y=1.1))
    return fig


def series_figure(result, pinned, selected):
    fig = go.Figure()
    for index, name in enumerate(selected):
        arrays = result["arrays"]
        value = arrays.get(f"series__{name}__v")
        if value is None:
            continue
        time = arrays.get(f"series__{name}__t")
        time = np.arange(len(value)) if time is None else time
        fig.add_trace(go.Scatter(x=time, y=value, name=name,
                                 line=dict(color=COLORS[index % len(COLORS)], width=1.6)))
    if pinned:
        for name, value in pinned.items():
            arrays = pinned[name]
            if f"series__{name}__v" in arrays:
                time = arrays.get(f"series__{name}__t", np.arange(len(arrays[f"series__{name}__v"])))
                fig.add_trace(go.Scatter(x=time, y=arrays[f"series__{name}__v"], name=f"pinned · {name}",
                                         line=dict(color="#666", width=1.2, dash="dash")))
    fig.update_layout(template="plotly_dark", height=360, margin=dict(l=10, r=10, t=30, b=10),
                      paper_bgcolor="#1a1a1a", plot_bgcolor="#1a1a1a", hovermode="x unified",
                      xaxis=dict(gridcolor="#2a2a2a"), yaxis=dict(gridcolor="#2a2a2a"),
                      legend=dict(orientation="h", y=1.12))
    return fig


def xy_figure(result, pinned):
    fig = go.Figure()
    arrays = result["arrays"]
    east, north = arrays.get("position__east"), arrays.get("position__north")
    if east is not None and north is not None:
        fig.add_trace(go.Scatter(x=east, y=north, mode="lines+markers", name="position",
                                 line=dict(color="#6cf", width=2), marker=dict(size=4)))
        fig.add_trace(go.Scatter(x=[east[0]], y=[north[0]], mode="markers", name="start",
                                 marker=dict(color="#5fd6a8", size=12, symbol="circle")))
        fig.add_trace(go.Scatter(x=[east[-1]], y=[north[-1]], mode="markers", name="end",
                                 marker=dict(color="#ff8f6b", size=12, symbol="square")))
    if pinned and "position__east" in pinned:
        fig.add_trace(go.Scatter(x=pinned["position__east"], y=pinned["position__north"],
                                 name="pinned", line=dict(color="#666", width=1.2, dash="dash")))
    fig.update_layout(template="plotly_dark", height=420, margin=dict(l=10, r=10, t=30, b=10),
                      paper_bgcolor="#1a1a1a", plot_bgcolor="#1a1a1a",
                      xaxis=dict(title="east (m)", gridcolor="#2a2a2a", scaleanchor="y", scaleratio=1),
                      yaxis=dict(title="north (m)", gridcolor="#2a2a2a"), legend=dict(orientation="h", y=1.1))
    return fig


def map_figure(result, yaw_offset=0.0, style="carto-darkmatter"):
    fig = go.Figure()
    arrays = result["arrays"]
    lat, lon = arrays.get("path__lat"), arrays.get("path__lon")
    if lat is not None and len(lat):
        fig.add_trace(go.Scattermap(lat=lat, lon=lon, mode="lines+markers", name="gps path",
                                    line=dict(color="#6cf", width=3), marker=dict(size=6)))
        fig.add_trace(go.Scattermap(lat=[lat[0]], lon=[lon[0]], mode="markers", name="start",
                                    marker=dict(size=14, color="#5fd6a8")))
    east, north = arrays.get("position__east"), arrays.get("position__north")
    if east is not None and lat is not None and len(lat) and lon is not None:
        angle = np.radians(yaw_offset)
        rot_e = east * np.cos(angle) - north * np.sin(angle)
        rot_n = east * np.sin(angle) + north * np.cos(angle)
        lat0, lon0 = float(lat[0]), float(lon[0])
        imu_lat, imu_lon = helpers_enu_to_gps(rot_e, rot_n, lat0, lon0)
        fig.add_trace(go.Scattermap(lat=imu_lat, lon=imu_lon, mode="lines", name="imu position",
                                    line=dict(color="#e8a33d", width=2)))
    if len(fig.data) == 0:
        return None
    center = {"lat": float(np.mean(lat)) if lat is not None and len(lat) else 0.0,
              "lon": float(np.mean(lon)) if lon is not None and len(lon) else 0.0}
    span = max(float(np.ptp(lat)) if lat is not None and len(lat) else 0.0,
               float(np.ptp(lon)) if lon is not None and len(lon) else 0.0, 1e-4)
    fig.update_layout(template="plotly_dark", height=520, margin=dict(l=0, r=0, t=0, b=0),
                      map=dict(style=style, center=center,
                               zoom=float(np.clip(np.log2(360.0 / span) - 2.0, 2, 18.5))),
                      legend=dict(orientation="h", y=0.02))
    return fig


def helpers_enu_to_gps(east, north, lat0, lon0):
    import helpers
    return helpers.enu_to_gps(east, north, lat0, lon0)


st.session_state.setdefault("loaded_code", TEMPLATE)
st.session_state.setdefault("current_code", TEMPLATE)
st.session_state.setdefault("editor_rev", 0)
st.session_state.setdefault("result", None)
st.session_state.setdefault("pinned", {})

with st.sidebar:
    st.title("🛰️ piFly analysis")
    sessions_dir = st.text_input("sessions dir", loader.DEFAULT_SESSIONS_DIR)
    sessions = loader.list_sessions(sessions_dir) if os.path.isdir(sessions_dir) else []
    if not sessions:
        st.error("no sessions found")
        st.stop()

    labels = {row["id"]: f"{row['id']} · {row['label']} · {row['records'] / 1e6:.2f}M rec"
                         f"{' · ' + row['name'] if row['name'] else ''}"
              for row in sessions}
    session_id = st.selectbox("session", list(labels), format_func=lambda key: labels[key], key="session_id")
    session = next(row for row in sessions if row["id"] == session_id)

    imu = loader.Imu(session["path"])
    span = imu.time_range()
    if span is None:
        st.error("empty imu.bin")
        st.stop()
    t0_epoch, t1_epoch = span
    duration = t1_epoch - t0_epoch

    st.caption(f"{session['records']:,} records · {duration / 60:.1f} min"
               f"{' · meta ✓' if session['has_meta'] else ' · no meta (killed)'}")

    whole = st.checkbox("whole session", value=False)
    slider_key = f"window_{session_id}"
    pending = st.session_state.pop("jump_target", None)
    if pending and pending[0] == session_id:
        st.session_state[slider_key] = (pending[1], pending[2])
    if whole:
        win0, win1 = 0.0, duration
    else:
        default_end = min(60.0, duration)
        win0, win1 = st.slider("window (s)", 0.0, float(duration), (0.0, float(default_end)),
                               step=1.0, key=slider_key)

    coverage = gps_coverage(session["path"])
    if coverage:
        gps_lo_s = max(0.0, coverage["t0"] - t0_epoch)
        gps_hi_s = min(duration, coverage["t1"] - t0_epoch)
        st.caption(f"session gps — {coverage['count']} fixes · {gps_lo_s / 60:.1f} → {gps_hi_s / 60:.1f} min")
    else:
        st.caption("session gps — none recorded")
    max_points = st.select_slider("max samples in window", [200_000, 500_000, 2_000_000, 5_000_000], value=2_000_000)
    timeout = st.slider("timeout (s)", 1.0, 120.0, 10.0, step=1.0)

    st.divider()
    gps_rows = cached_jsonl(session["path"], "gps", t0_epoch + win0, t0_epoch + win1, 500_000)
    bme_rows = cached_jsonl(session["path"], "bme", t0_epoch + win0, t0_epoch + win1, 500_000)
    mark_rows = cached_jsonl(session["path"], "marks", t0_epoch + win0, t0_epoch + win1, 5_000)
    st.caption(f"in window — gps {len(gps_rows)} · bme {len(bme_rows)} · marks {len(mark_rows)}")
    if coverage and not gps_rows and not whole:
        st.warning(f"no GPS fixes in this window — they start at {gps_lo_s / 60:.1f} min")
        if st.button("⤓ jump to GPS data", width="stretch"):
            start = max(0.0, gps_lo_s - 15.0)
            st.session_state["jump_target"] = (session_id, start, min(duration, start + 60.0))
            st.rerun()

    st.divider()
    col_a, col_b = st.columns(2)
    if col_a.button("▶ Run", width="stretch", type="primary"):
        st.session_state["run_requested"] = True
    if col_b.button("📌 Pin", width="stretch"):
        if st.session_state["result"] and st.session_state["result"]["ok"]:
            manifest = st.session_state["result"]["manifest"]
            st.session_state["pinned"] = {
                **{f"series__{name}__v": st.session_state["result"]["arrays"][f"series__{name}__v"]
                   for name in manifest.get("series", {}) if f"series__{name}__v" in st.session_state["result"]["arrays"]},
                **{key: value for key, value in st.session_state["result"]["arrays"].items()
                   if key.startswith("position__")},
            }
    if st.button("🗑 Clear pin", width="stretch"):
        st.session_state["pinned"] = {}

left, right = st.columns([1.35, 2.0], gap="medium")

with left:
    st.subheader("Function")
    if HAS_EDITOR:
        response = code_editor(
            st.session_state["loaded_code"],
            lang="python",
            theme="dark",
            shortcuts="vscode",
            height=[18, 30],
            key=f"editor_{st.session_state['editor_rev']}",
            info={"name": "process(x)", "description": "x: window arrays · return dict with path/position/velocity/series/stats"},
        )
        if response and response.get("text"):
            st.session_state["current_code"] = response["text"]
        code_now = st.session_state["current_code"]
        if response and response.get("type") == "submit":
            st.session_state["run_requested"] = True
    else:
        edited = st.text_area("process(x)", st.session_state["current_code"], height=420,
                              key=f"fallback_{st.session_state['editor_rev']}")
        st.session_state["current_code"] = edited
        code_now = edited

    with st.expander("inputs & units"):
        st.markdown(
            "- `x['t']` seconds from window start, `x['t_epoch']` absolute\n"
            "- `ax..az` m/s², `gx..gz` rad/s, `x['fs']` nominal Hz\n"
            "- `x['gps']` = t, lat, lon, alt, sats, speed_kt, course (or None)\n"
            "- `x['bme']` = t, temp_c, pressure_hpa, humidity_pct\n"
            "- helpers: `bucket, movavg, ema, lowpass, highpass, integrate, gps_to_enu, "
            "enu_to_gps, pressure_to_alt, tilt_from_accel, complementary, mahony, zupt_mask, "
            "remove_gravity, body_to_world`"
        )

    cols = st.columns(2)
    snippet_files = sorted(f for f in os.listdir(loader.SNIPPETS_DIR) if f.endswith(".py"))
    choice = cols[0].selectbox("snippet", snippet_files, label_visibility="collapsed")
    if cols[1].button("load", width="stretch"):
        with open(os.path.join(loader.SNIPPETS_DIR, choice)) as handle:
            st.session_state["loaded_code"] = handle.read()
        st.session_state["current_code"] = st.session_state["loaded_code"]
        st.session_state["editor_rev"] += 1
        st.rerun()

    name = st.text_input("save as", value="", placeholder="my_aggregation")
    if st.button("💾 save snippet", width="stretch"):
        target = os.path.join(loader.SNIPPETS_DIR, (name or "snippet").replace(" ", "_") + ".py")
        with open(target, "w") as handle:
            handle.write(st.session_state["current_code"])
        st.success(f"saved {os.path.basename(target)}")

    result = st.session_state["result"]
    if result and result["ok"]:
        st.caption(f"ran in {result['runtime']:.2f}s · {len(result['arrays'])} arrays")
        if st.session_state.get("last_run_code") != st.session_state["current_code"]:
            st.warning("code changed — press ▶ Run")
    elif result and not result["ok"]:
        st.error("run failed")
        st.code(result["error"], language="text")
    if result and result.get("stdout"):
        with st.expander("stdout"):
            st.code(result["stdout"], language="text")

run_key = (session_id, round(win0, 2), round(win1, 2), bool(whole), int(max_points))
run_requested = st.session_state.pop("run_requested", False)
stale = st.session_state.get("last_run_key") != run_key
if (run_requested or stale) and session["records"]:
    with st.spinner("running…"):
        window = imu.window(t0_epoch + win0, t0_epoch + win1, max_points=max_points)
        if window is None:
            st.warning("empty window")
        else:
            arrays = executor.build_arrays(
                window,
                loader.gps_bundle(gps_rows, t0_epoch + win0),
                loader.bme_bundle(bme_rows, t0_epoch + win0),
                loader.marks_bundle(mark_rows, t0_epoch + win0),
                fs=window["stride"] * 1000.0 if window["stride"] > 1 else 1000.0,
                session_id=session_id, epoch0=t0_epoch + win0,
            )
            outcome = executor.run_user_code(st.session_state["current_code"], arrays, timeout=timeout)
            outcome["session_id"] = session_id
            outcome["window"] = (win0, win1)
            outcome["stride"] = window["stride"]
            outcome["samples"] = len(window["t"])
            st.session_state["result"] = outcome
            st.session_state["last_run_key"] = run_key
            st.session_state["last_run_code"] = st.session_state["current_code"]
            result = outcome

result = st.session_state["result"]
pinned = st.session_state["pinned"]

with right:
    if not result or not result["ok"]:
        st.info("edit the function on the left and press ▶ Run (or Ctrl+Enter in the editor)")
    else:
        manifest = result["manifest"]
        tabs = st.tabs(["📈 results", "🗺 map", "🧮 raw window", "📋 stats"])
        with tabs[0]:
            names = list(manifest.get("series", {}))
            if names:
                selected = st.multiselect("series", names, default=names, label_visibility="collapsed")
                st.plotly_chart(series_figure(result, pinned, selected), width="stretch")
            for group, fields in (("velocity", ("vx", "vy", "vz")), ("path", ("alt",))):
                if group in manifest:
                    fig = go.Figure()
                    for index, field in enumerate(fields):
                        key = f"{group}__{field}"
                        if key in result["arrays"]:
                            value = result["arrays"][key]
                            if group == "velocity" and field in ("vx", "vy"):
                                value = np.hypot(result["arrays"]["velocity__vx"], result["arrays"]["velocity__vy"]) \
                                    if field == "vx" else np.degrees(np.arctan2(result["arrays"]["velocity__vy"],
                                                                                result["arrays"]["velocity__vx"]))
                                label = "speed (m/s)" if field == "vx" else "heading (deg)"
                            else:
                                label = f"{group} {field}"
                            time = result["arrays"].get(f"{group}__t", np.arange(len(value)))
                            fig.add_trace(go.Scatter(x=time, y=value, name=label,
                                                     line=dict(color=COLORS[index % len(COLORS)], width=1.6)))
                    fig.update_layout(template="plotly_dark", height=240, margin=dict(l=10, r=10, t=20, b=10),
                                      paper_bgcolor="#1a1a1a", plot_bgcolor="#1a1a1a", hovermode="x unified",
                                      xaxis=dict(gridcolor="#2a2a2a"), yaxis=dict(gridcolor="#2a2a2a"),
                                      legend=dict(orientation="h", y=1.15))
                    st.plotly_chart(fig, width="stretch")
            if "position" in manifest:
                st.plotly_chart(xy_figure(result, pinned), width="stretch")
            if not names and "position" not in manifest and "path" not in manifest:
                st.warning("nothing plotted — return series / position / path / velocity")
        with tabs[1]:
            map_cols = st.columns([1, 1])
            yaw = map_cols[0].slider("imu path rotation (deg)", -180.0, 180.0, 0.0, step=5.0)
            style = map_cols[1].selectbox("basemap", ["carto-darkmatter", "open-street-map",
                                                      "carto-positron", "white-bg (no tiles)"])
            figure = map_figure(result, yaw, "white-bg" if style.startswith("white") else style)
            if figure is None:
                if coverage and not gps_rows:
                    st.info(f"no GPS fixes in this window — this session has {coverage['count']} fixes from "
                            f"{gps_lo_s / 60:.1f} to {gps_hi_s / 60:.1f} min of the recording")
                elif not coverage:
                    st.info("this session has no GPS data at all")
                else:
                    st.info("your result has no `path`/`position` — return one to draw it here")
            else:
                st.plotly_chart(figure, width="stretch")
                lat = result["arrays"].get("path__lat")
                lon = result["arrays"].get("path__lon")
                if lat is not None and len(lat) > 1:
                    width_m = float(np.ptp(lon)) * 111_320 * np.cos(np.radians(np.mean(lat)))
                    height_m = float(np.ptp(lat)) * 110_540
                    note = " · stationary data: this is GPS noise around one point" if max(width_m, height_m) < 50 else ""
                    st.caption(f"{len(lat)} fixes · extent {width_m:.0f} m × {height_m:.0f} m{note}")
        with tabs[2]:
            st.caption(f"{result['samples']:,} samples in window (stride {result['stride']})")
            st.plotly_chart(signal_figure(imu.window(t0_epoch + win0, t0_epoch + win1, max_points=200_000),
                                          loader.marks_bundle(mark_rows, t0_epoch + win0)),
                            width="stretch")
        with tabs[3]:
            stats = manifest.get("stats", {})
            info = {"runtime_s": round(result["runtime"], 3), "samples": result["samples"],
                    "stride": result["stride"], "series": len(manifest.get("series", {})),
                    "window_s": round(result["window"][1] - result["window"][0], 1)}
            info.update(stats)
            st.dataframe(pd.DataFrame([info]).T.rename(columns={0: "value"}), width="stretch")
            if manifest.get("ignored"):
                st.caption(f"ignored keys: {', '.join(manifest['ignored'])}")
            arrays = result["arrays"]
            series_name = st.selectbox("export", ["series", "position", "path", "velocity"],
                                       format_func=lambda g: g if g in manifest else f"{g} (missing)")
            if series_name in manifest:
                if series_name == "series":
                    frame = padded_frame({name: arrays.get(f"series__{name}__v")
                                          for name in manifest["series"]})
                else:
                    frame = padded_frame({field: arrays.get(f"{series_name}__{field}")
                                          for field in manifest[series_name]})
                st.download_button("⬇ download CSV", frame.to_csv(index=False),
                                   file_name=f"{session_id}_{series_name}.csv", mime="text/csv")
