#!/usr/bin/env python3
"""Logic-analyzer view of RSL15 pads from a `sim --trace-out` file, in the terminal.

usage: pads.py TRACE PIN[=LABEL] ... [--from MS] [--to MS] [--cols N]
Each pin is one row: '▔' high, '▁' low, '│' a column holding an edge. Times are chip time.
"""
import json, sys

args = sys.argv[1:]
trace = args.pop(0)
opt = {"--from": 0.0, "--to": None, "--cols": 100, "--intervals": 0}
pins = []
while args:
    a = args.pop(0)
    if a == "--intervals":
        opt[a] = 1
    elif a in opt:
        opt[a] = float(args.pop(0))
    else:
        p, _, label = a.partition("=")
        pins.append((int(p), label or f"GPIO{p}"))
edges = {p: [] for p, _ in pins}
for line in open(trace):
    r = json.loads(line)
    if r.get("pin") in edges:
        edges[r["pin"]].append((r["t"] * 1e3, r["level"]))
if opt["--intervals"]:
    for p, label in pins:
        t = [e[0] for e in edges[p]]
        runs = []
        for d in (round(b - a, 1) for a, b in zip(t, t[1:])):
            if runs and runs[-1][0] == d:
                runs[-1][1] += 1
            else:
                runs.append([d, 1])
        print(f"{label}: " + "  ".join(f"{d} ms x{n}" for d, n in runs))
    sys.exit(0)
t0 = opt["--from"]
t1 = opt["--to"] or max((e[-1][0] for e in edges.values() if e), default=t0 + 1)
cols = int(opt["--cols"])
dt = (t1 - t0) / cols
width = max(len(l) for _, l in pins)
for p, label in pins:
    ev = edges[p]
    level = next((lv for t, lv in reversed(ev) if t <= t0), 1 - ev[0][1] if ev else 0)
    row, k = [], 0
    while k < len(ev) and ev[k][0] <= t0:
        k += 1
    for c in range(cols):
        hi = t0 + (c + 1) * dt
        changed = False
        while k < len(ev) and ev[k][0] < hi:
            level, changed, k = ev[k][1], True, k + 1
        row.append("│" if changed else ("▔" if level else "▁"))
    print(f"{label:>{width}} {''.join(row)}")
print(f"{'':>{width}} {t0:<10.3f}{'':{cols - 20}}{t1:>10.3f} ms")
