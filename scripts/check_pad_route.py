"""Check which pads carry what, in order, from a `sim --trace-out` GPIO trace.

For GPIO-matrix fixtures: the firmware routes a register-fixed PWM waveform
(period = 2^DUTY_RES ticks, high = DUTY ticks) to one pad after another and
drives others as plain outputs. Each expected state names every watched pad
as "low", "high" or a {"pwm": {period_us, period_ticks, high_ticks}}; the
checker finds the states in order, each holding for at least `hold_ms` of
trace time. Phase durations are firmware timing and are not asserted.

Usage: check_pad_route.py <trace.jsonl> <port> <expect-json> [--json <report>]
  expect-json: {"pins": [18, 17, 16, 47], "hold_ms": 20,
                "states": [{"18": {"pwm": {...}}, "17": "low", ...}, ...]}
"""
import bisect
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_pad_timing import load_edges, tick_tol, PERIOD_TOL_PPM  # noqa: E402


class Pad:
    def __init__(self, trace, port, pin):
        self.t, self.lv = load_edges(trace, port, pin)

    def level_at(self, t):
        i = bisect.bisect_right(self.t, t) - 1
        return self.lv[i] if i >= 0 else 0

    def edges_in(self, t0, t1):
        a, b = bisect.bisect_right(self.t, t0), bisect.bisect_left(self.t, t1)
        return list(range(a, b))

    def matches(self, want, t0, t1):
        """-> (ok, measured) for this pad over [t0, t1)."""
        idx = self.edges_in(t0, t1)
        if want in ("low", "high"):
            lvl = self.level_at(t0)
            ok = not idx and lvl == (1 if want == "high" else 0)
            return ok, {"level": lvl, "edges": len(idx)}
        pwm = want["pwm"]
        period = pwm["period_us"] * 1e-6
        tick = period / pwm["period_ticks"]
        rises = [self.t[i] for i in idx if self.lv[i] == 1]
        falls = [self.t[i] for i in idx if self.lv[i] == 0]
        per, high = [], []
        for r0, r1 in zip(rises, rises[1:]):
            j = bisect.bisect_right(falls, r0)
            if j < len(falls) and falls[j] < r1:
                per.append(r1 - r0)
                high.append(falls[j] - r0)
        if len(per) < 5:
            return False, {"cycles": len(per)}
        p, h = st.median(per), st.median(high) / tick
        ok = (abs(p - period) <= period * PERIOD_TOL_PPM * 1e-6
              and abs(h - pwm["high_ticks"]) <= tick_tol(tick)
              and max(per) - min(per) <= period * 0.02)
        return ok, {"cycles": len(per), "period_us": round(p * 1e6, 4), "high_ticks": round(h, 2)}


def check(trace, port, spec):
    pads = {p: Pad(trace, port, p) for p in spec["pins"]}
    hold = spec.get("hold_ms", 20) * 1e-3
    # Candidate window starts: the cursor, then a quarter-hold grid to the end
    # of the trace (a state that holds for `hold` is seen from some grid point).
    # The end is the trace's last record, not the last edge on a watched pad:
    # a final state where every pad sits still has no edges of its own.
    end = 0.0
    with open(trace) as f:
        for line in f:
            t = json.loads(line).get("t")
            if t is not None and t > end:
                end = t
    starts = [i * hold / 4 for i in range(int(end / (hold / 4)) + 1)]
    cursor, rows, ok = 0.0, [], True
    for n, state in enumerate(spec["states"]):
        found = None
        for t0 in [cursor] + [t for t in starts if t > cursor]:
            res = {pin: pads[pin].matches(state[str(pin)], t0, t0 + hold) for pin in pads}
            if all(r[0] for r in res.values()):
                found = (t0, res)
                break
        if found is None:
            rows.append({"state": n, "expect": state, "error": "never held for hold_ms"})
            ok = False
            continue
        t0, res = found
        rows.append({"state": n, "at_s": round(t0, 6), "pads": {str(p): r[1] for p, r in res.items()}})
        cursor = t0 + hold
    return ok, rows


def main():
    trace, port, spec = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    report = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    ok, rows = check(trace, port, spec)
    out = {"ok": ok, "port": port, "states": rows}
    if report:
        Path(report).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
