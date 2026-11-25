"""Check the edge timing of one GPIO pad in a `sim --trace-out` trace, to nanoseconds.

For timer/PWM fixtures whose firmware prints nothing (the unmodified STM32Cube
examples). Every expected number is what the registers the firmware wrote
dictate, never a firmware timing. Unlike check_pad_timing.py (ESP32 LEDC: 100 ns
edge quantum, 10 ns glitch filter) nothing is filtered or quantised here: the
trace carries 1 ns timestamps and the engine delivers an edge 0..999 ps after
the exact register-derived instant (rn-engine stm32g4_hrtim.rs, "Timing
uncertainty bounds"), so `tol_ns` defaults to 1.0 per edge-to-edge measurement.

Usage: check_pad_edges.py <trace.jsonl> <spec-json> [--json <report>]

spec: {"port": "gpioPortC", "pin": 6, "machine": "dut",   # machine optional
       "tol_ns": 1.0,
       "cases": [ ... ]}   # matched in order; each starts where the last ended

cases:
  {"periodic": {"period_ns": 500, "high_ns": 65, "min_cycles": 20}}
      N consecutive rising-to-rising periods of `period_ns` with a high time of
      `high_ns`, each within tol_ns, and the N-period span within tol_ns in total
      (so a rate error cannot hide inside the per-edge tolerance).
  {"intervals_ns": [a, b, c, ...], "min_repeats": 3}
      successive edge-to-edge gaps follow the list, repeated min_repeats times,
      starting at any rotation (the pattern's phase is not asserted).
  {"lag": {"ref": {"port": "gpio", "pin": 2}, "lag_ns": 6250, "min_cycles": 20}}
      N consecutive rising edges each `lag_ns` after the reference pad's latest
      rising edge (a PWM channel offset against channel 0).
  {"level": 0, "min_ns": 100000}
      the pad holds `level` for at least `min_ns`.
  {"level": 0, "window_ns": [2100000, 2600000]}
      absolute: the pad is at `level` at the window start and has no edge until its
      end. Anchored to a stimulus time (e.g. --gpio-in), so it holds under a
      constant input-to-output delay and cannot pass by running out of trace.
"""
import bisect
import json
import sys


def load_edges(path, port, pin, machine=None):
    """-> (times_ns, levels): level changes only, in trace order."""
    t, lv = [], []
    ptag, mtag = f'"pin":{pin},', f'"machine":"{machine}"' if machine else None
    with open(path) as f:
        for line in f:
            if ptag not in line or f'"port":"{port}"' not in line:
                continue
            if mtag and mtag not in line:
                continue
            r = json.loads(line)
            if r.get("kind") != "gpio":
                continue
            level = int(r["level"])
            if not lv or level != lv[-1]:
                t.append(round(float(r["t"]) * 1e9, 3))
                lv.append(level)
    return t, lv


def match_periodic(t, lv, start, c, tol):
    period, high, n = c["period_ns"], c["high_ns"], int(c.get("min_cycles", 10))
    for i in range(start, len(t) - 2 * n):
        if lv[i] != 1:
            continue
        ok = True
        for k in range(n):
            r, f, r2 = t[i + 2 * k], t[i + 2 * k + 1], t[i + 2 * k + 2]
            if abs((r2 - r) - period) > tol or abs((f - r) - high) > tol:
                ok = False
                break
        if ok and abs((t[i + 2 * n] - t[i]) - n * period) <= tol:
            return i + 2 * n, f"periodic {period:g}/{high:g} ns x{n} at {t[i]:.0f} ns"
    return None, None


def match_intervals(t, lv, start, c, tol):
    pat, reps = c["intervals_ns"], int(c.get("min_repeats", 3))
    m = len(pat)
    gaps = [t[j + 1] - t[j] for j in range(len(t) - 1)]
    for s in range(start, len(gaps) - m * reps + 1):
        for rot in range(m):
            if all(abs(gaps[s + j] - pat[(rot + j) % m]) <= tol for j in range(m * reps)):
                return s + m * reps, f"intervals {pat} x{reps} at {t[s]:.0f} ns"
    return None, None


def match_lag(t, lv, start, c, tol, ref):
    lag, n = c["lag_ns"], int(c.get("min_cycles", 10))
    rises = [x for x, l in zip(*ref) if l == 1]
    run = 0
    for i in range(start, len(t)):
        if lv[i] != 1:
            continue
        k = bisect.bisect_right(rises, t[i])
        run = run + 1 if k and abs(t[i] - rises[k - 1] - lag) <= tol else 0
        if run == n:
            return i + 1, f"lag {lag:g} ns after {c['ref']['port']}.{c['ref']['pin']} x{n}"
    return None, None


def match_level(t, lv, start, c, tol):
    want = int(c["level"])
    if "window_ns" in c:
        lo, hi = c["window_ns"]
        i = max((k for k in range(len(t)) if t[k] <= lo), default=None)
        held = lv[i] if i is not None else 0  # a pad that never moved sits at its reset level
        quiet = not any(lo < x <= hi for x in t)
        if held == want and quiet:
            return start, f"level {want} held over [{lo:.0f}, {hi:.0f}] ns"
        return None, None
    need = c["min_ns"]
    for i in range(start, len(t)):
        if lv[i] == want:
            end = t[i + 1] if i + 1 < len(t) else float("inf")
            if end - t[i] >= need:
                return i + 1, f"level {want} held {min(end - t[i], 1e18):.0f} ns at {t[i]:.0f} ns"
    return None, None


MATCHERS = {"periodic": match_periodic, "lag": match_lag, "intervals_ns": match_intervals, "level": match_level}


def main(argv):
    path, spec = argv[1], json.loads(argv[2])
    t, lv = load_edges(path, spec["port"], int(spec["pin"]), spec.get("machine"))
    tol = float(spec.get("tol_ns", 1.0)) + 1e-6
    at, notes = 0, []
    for n, case in enumerate(spec["cases"]):
        key = next(k for k in MATCHERS if k in case)
        if key == "lag":
            ref = case["lag"]["ref"]
            refs = load_edges(path, ref["port"], int(ref["pin"]), spec.get("machine"))
            nxt, note = match_lag(t, lv, at, case["lag"], tol, refs)
        else:
            arg = case[key] if key == "periodic" else case
            nxt, note = MATCHERS[key](t, lv, at, arg, tol)
        if nxt is None:
            tail = [round(t[j + 1] - t[j], 1) for j in range(at, min(at + 16, len(t) - 1))]
            print(f"case {n} ({key}) not found after edge {at} of {len(t)} on "
                  f"{spec['port']}.{spec['pin']}; first gaps there (ns): {tail}")
            return 1
        at = nxt
        notes.append(note)
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w") as f:
            json.dump({"edges": len(t), "matched": notes}, f)
    print(f"{spec['port']}.{spec['pin']}: " + "; ".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
