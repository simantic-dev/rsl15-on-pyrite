"""Check a `sim --trace-out` GPIO pad trace against register-derived LEDC
hardware quantities.

The pad waveform is reduced to one (period, high time) pair per PWM cycle,
then split into ramps (high time changing cycle to cycle) and plateaus
(>= 5 cycles at the same high time). Each `pad: expect:` case in test.yaml
must match, in order: a plateau's period and high time, and for a fade, the
size and count of the steps that led into it. Every expected number is what
the LEDC registers the driver wrote dictate -- period = 2^DUTY_RES ticks,
high = DUTY ticks, step = DUTY_SCALE ticks every DUTY_CYCLE periods for
DUTY_NUM steps -- never a firmware timing (ISR latency, fade-end callback,
esp_timer). See tests/esp/esp32s3/ledc-duty-timing/silicon-saleae/README.md
for the S3 silicon comparison these tolerances come from.

Usage: check_pad_timing.py <trace.jsonl> <port> <pin> <expect-json> [--json <report>]

`port` is the trace record's "port" (the GPIO controller the pad belongs
to, "gpio" on every ESP32); the trace also carries interrupt-matrix and CLIC
lines under the same pin numbers, which must not be mixed in.
"""
import bisect
import json
import statistics as st
import sys

PLATEAU = 5           # cycles at one high time = steady state
EDGE_QUANTUM_S = 1e-7  # Renode virtual time is 100 ns-granular (TimeInterval)
PERIOD_TOL_PPM = 50   # sim clocks are exact; this is the 100 ns floor at 200 us
GLITCH_S = 10e-9      # below one 80 MHz LEDC clock (12.5 ns): no LEDC output can
                      # be this narrow, so it is analyzer ringing (silicon traces).
                      # A zero-width pair is kept: that is a real sub-100 ns pulse
                      # the simulator's edge quantum collapsed, not ringing.


def tick_tol(tick_s):
    """Tolerance in ticks for one edge-to-edge measurement: one LEDC tick,
    or one edge quantum when a tick is finer than the simulator's clock
    (24.4 ns at 5 kHz / 13-bit, so ~4 ticks)."""
    return max(1.0, EDGE_QUANTUM_S / tick_s)


def load_edges(path, port, pin):
    t, lv = [], []
    with open(path) as f:
        for line in f:
            if f'"pin":{pin},' not in line:      # cheap prefilter: traces can run to GB
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "gpio" or r.get("port") != port or int(r.get("pin", -1)) != pin:
                continue
            level = int(r["level"])
            if not t or level != lv[-1]:
                t.append(float(r["t"]))
                lv.append(level)
    # Drop pulses narrower than GLITCH_S (both edges), repeating until none.
    while True:
        keep_t, keep_l, i, dropped = [], [], 0, 0
        while i < len(t):
            if i + 1 < len(t) and 0 < t[i + 1] - t[i] < GLITCH_S:
                i, dropped = i + 2, dropped + 1
                continue
            keep_t.append(t[i]); keep_l.append(lv[i]); i += 1
        t, lv = keep_t, keep_l
        if not dropped:
            return t, lv


def cycles(t, lv):
    """-> [(period_s, high_s)] per rising edge that has a next rising edge."""
    rise = [t[i] for i in range(len(t)) if lv[i] == 1]
    fall = [t[i] for i in range(len(t)) if lv[i] == 0]
    out = []
    for i in range(len(rise) - 1):
        j = bisect.bisect_right(fall, rise[i])
        high = fall[j] - rise[i] if j < len(fall) and fall[j] < rise[i + 1] else rise[i + 1] - rise[i]
        out.append((rise[i + 1] - rise[i], high))
    return out


def plateaus(cyc, tick_of, tol):
    """Split the cycle list into (ramp, plateau) pairs.

    A plateau is >= PLATEAU consecutive cycles whose high time (in that
    cycle's own tick) is within tol of the first; the ramp is every
    cycle since the previous plateau ended. The trace's end counts as a
    plateau boundary so a fade that ends at duty 0 (no more edges) closes.
    """
    out, ramp, i = [], [], 0
    while i < len(cyc):
        k = i
        while (k + 1 < len(cyc) and abs(tick_of(cyc[k + 1]) - tick_of(cyc[i])) <= tol
               and abs(cyc[k + 1][0] - cyc[i][0]) <= cyc[i][0] * 0.02):
            k += 1
        if k - i + 1 >= PLATEAU:
            out.append((ramp, cyc[i:k + 1]))
            ramp = []
        else:
            ramp.extend(cyc[i:k + 1])
        i = k + 1
    if ramp:
        out.append((ramp, []))   # ramp that ran off the end (fade to 0)
    return out


def slope(ys):
    """Least-squares ticks per cycle; the 100 ns edge quantum averages out
    over a ramp, so this resolves steps finer than one quantum (8 ticks
    = 195 ns for a 200 ms full-scale fade)."""
    n = len(ys)
    if n < 2:
        return 0.0
    mx, my = (n - 1) / 2, sum(ys) / n
    return sum((i - mx) * (y - my) for i, y in enumerate(ys)) / sum((i - mx) ** 2 for i in range(n))


def high_time_fn(t, lv):
    """-> H(x): total time the pad has been high up to x (level lv[i] holds from t[i])."""
    cum = [0.0]
    for i in range(len(t) - 1):
        cum.append(cum[-1] + (t[i + 1] - t[i] if lv[i] else 0.0))

    def H(x):
        i = bisect.bisect_right(t, x) - 1
        return 0.0 if i < 0 else cum[i] + (x - t[i] if lv[i] else 0.0)
    return H


def check_density(t, lv, d):
    """Pulse-density (sigma-delta) check, for GPIO_SD pads. All numbers come from
    the TRM: density = (SDn_IN + 128) / 256, one pulse per prescaled APB cycle.
    d = {"period_us": P, "mean": m, "tol": e,           # optional: mean high fraction over every whole
         "skip_periods": k,                             # P-long window after the first k (the
                                                        # first edge precedes the waveform)
         "window_us": W, "peak": [lo, hi], "trough": [lo, hi],   # max/min sliding W-window density
         "pulse_ns": n}                                 # shortest pulse, = 1 prescaled cycle
    """
    H = high_time_fn(t, lv)
    a0, end = t[0], t[-1]
    row, ok = {}, True
    if "period_us" in d:
        P = d["period_us"] * 1e-6
        means = [(H(a0 + (k + 1) * P) - H(a0 + k * P)) / P for k in range(d.get("skip_periods", 0), int((end - a0) / P))]
        row["period_means"] = [round(m, 4) for m in means]
        if not means or any(abs(m - d["mean"]) > d["tol"] for m in means):
            ok = False
    W = d["window_us"] * 1e-6
    step = W / 10
    dens = [(H(x + W) - H(x)) / W for x in (a0 + k * step for k in range(int((end - a0 - W) / step)))]
    row["window_max"], row["window_min"] = round(max(dens), 4), round(min(dens), 4)
    if not d["peak"][0] <= row["window_max"] <= d["peak"][1] or not d["trough"][0] <= row["window_min"] <= d["trough"][1]:
        ok = False
    widths = [(t[i + 1] - t[i]) * 1e9 for i in range(len(t) - 1)]
    row["shortest_pulse_ns"] = round(min(widths), 1)
    if abs(min(widths) - d["pulse_ns"]) > 1.0:
        ok = False
    row["ok"] = ok
    return ok, [row]


def check(trace, port, pin, expects):
    t, lv = load_edges(trace, port, pin)
    if not t:
        return False, [{"error": f"no edges on {port} pin {pin}"}]
    if "density" in expects[0]:
        return check_density(t, lv, expects[0]["density"])
    cyc = cycles(t, lv)
    ok, rows, runs, before = True, [], None, 0.0
    for exp in expects:
        period = exp["period_us"] * 1e-6
        tick = period / exp["period_ticks"]
        tol = tick_tol(tick)
        if runs is None:
            runs = plateaus(cyc, lambda c: c[1] / tick, tol)
        # Find the next plateau at this case's period and final high time.
        want_high = exp["high_ticks"]
        hit = None
        while runs:
            ramp, plat = runs.pop(0)
            if plat:
                got_p = st.median(p for p, _ in plat)
                got_h = st.median(h for _, h in plat) / tick
            else:
                got_p, got_h = period, 0.0    # edges stopped: output parked low
            if abs(got_p - period) <= period * PERIOD_TOL_PPM * 1e-6 and abs(got_h - want_high) <= tol:
                hit = (ramp, got_p, got_h)
                break
        if hit is None:
            rows.append({"case": exp, "error": "no plateau at this period and high time"})
            ok = False
            continue
        ramp, got_p, got_h = hit
        row = {"period_us": round(got_p * 1e6, 4), "high_ticks": round(got_h, 2), "ok": True}
        fade = exp.get("fade")
        if fade:
            # The ramp is every cycle at this period between the previous
            # plateau and this one, less any cycles still parked at either
            # level (a timer reconfigure leaves cycles at the old period in
            # front; the fade-start latency leaves a few at the start duty).
            hs = [h / tick for p, h in ramp if abs(p - period) <= period * 0.02]
            while hs and abs(hs[0] - before) <= tol:
                hs.pop(0)
            while hs and abs(hs[-1] - got_h) <= tol:
                hs.pop()
            # One step of DUTY_SCALE per period (DUTY_CYCLE=1) for DUTY_NUM
            # periods; the driver's final snap to the exact target is the
            # plateau's first cycle, already trimmed above.
            step = abs(slope(hs))
            row["fade"] = {"step_ticks": round(step, 2), "steps": len(hs)}
            if abs(step - fade["step_ticks"]) > 1.0 or abs(len(hs) - fade["steps"]) > 2:
                row["ok"] = False
                ok = False
        before = got_h
        rows.append(row)
    return ok, rows


def main():
    trace, port, pin, expects = sys.argv[1], sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4])
    report = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    ok, rows = check(trace, port, pin, expects)
    out = {"ok": ok, "port": port, "pin": pin, "cases": rows}
    if report:
        with open(report, "w") as f:
            json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
