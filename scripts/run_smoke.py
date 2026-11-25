#!/usr/bin/env python3
"""Run the sim-fixtures firmware smoke tests against a published `sim` binary.

Each test lives in tests/<vendor>/<model>/<name>/ with a test.yaml manifest.

Single-machine fixtures name one MCU directly:

    mcu: STM32F401RE            # model the backend serves
    overlay: overlay.board      # optional; board file passed to sim with the model
    elf: nucleo_f401re.elf
    elfs: [core0.elf, core1.elf]  # instead of elf: one image per core, in core order (pyrite only;
                                  # AM2634's four R5Fs); each loads where its core sees it
    elf_external: true          # optional; ELF is a third-party build, not vendored --
                                #   absent means SKIP-NOELF, not a failure
    external_files: {sdcard.img: <sha256>}  # optional; large blobs kept out of git. Each is taken from
                                #   $SIM_FIXTURES_CACHE (default ~/.cache/sim-fixtures)/<sha256> and
                                #   symlinked into the test dir; absent means SKIP-NOELF, a hash
                                #   mismatch fails. The fixture's fetch.sh puts it in the cache.
    timeout: 6                  # virtual seconds the guest may run
    wireless: true              # optional; picks the wall-clock ceiling
    wall: 100                   # optional; explicit wall-clock ceiling in seconds (wins over wireless)
    pad:                        # optional; GPIO pad waveform check (scripts/check_pad_timing.py)
      pin: 18                   #   pad recorded via `sim --trace-out` gpio records
      port: gpio                #   optional; the trace "port" the pad belongs to
    pad_edges:                  # optional; ns-resolution edge timing of a pad, or a list of pads
                                #   (scripts/check_pad_edges.py); counts as the fixture's assertion,
                                #   so `expect:` may be empty. `timeout:` may be fractional seconds
                                #   (pyrite sim with fractional --timeout).
    pad_route:                  # optional; which pads carry what, in order (scripts/check_pad_route.py)
      expect: [...]             #   register-derived period/high/fade per case
    sparse_files: {sdcard.img: 4G}  # optional; blank sparse files created in the work dir per run,
                                    # reachable from the overlay as {WORK_DIR}/sdcard.img
    fat16_files: {sd.img: 64M}      # optional; empty FAT16 volumes (scripts/make_fat16.py), same place
    sim_args:      ["--usb-cdc-console", "usb_otg_fs"]  # optional; extra `sim` args, appended verbatim
    pytest: test_usb_raw.py     # optional; host side is a pytest file run with --sim <binary>;
                                #   pass = pytest exits 0, and `expect:` is not used
    defmt: true                 # optional; capture RTT up-channel 0 and decode it with `defmt-print`
    rtt: true                   # optional; capture RTT up-channel 0 as text (a probe's RAM log), checked with the UART
                                #   (Embassy tests), appending the log lines to the checked output
    trace_interrupts: balanced # optional; adds --trace-interrupts, then every trap name must
                               #   have as many exits as entries (see trace_balance_failure)
    preload: [eeprom.hex, "cal.bin@0x607C0000"]  # optional; non-volatile images written after the ELF loads (sim --preload):
                               #   a raw binary needs @ADDR, Intel HEX / ELF / UF2 name their own; paths are fixture-relative.
                               #   scripts/teensy_eeprom_image.py builds a Teensy 4.x EEPROM image
    analog: [{adc: adc1, channel: 11, points: [[0, 3.0]]}]  # optional (scenario machine); ADC input volts over time
    seed: 7                    # optional (scenario); RNG seed for impairments and hardware RNG models
    serialExecution: true      # optional (scenario); deterministic core/machine interleaving
    expect_frames: ["BLE Corrupted"]        # optional (scenario); substrings of the --frames file
    expect_frames_absent: ["reason=collision"]
    expect_log: ["[gcs] MAVLINK ATTITUDE"]  # optional; substrings of sim's stderr + stdout (peers log there; so do --trace-memory/--trace-symbol)
    expect_log_absent: ["MAVLINK bad crc"]
    expect:        ["RESULT: PASS"]
    expect_absent: ["RESULT: FAIL"]
    expect_bands:               # optional; numbers the UART prints, each inside [min, max]: `re` has one
      - {re: "run=(\\d+)", min: 900, max: 1100}  # group, read from its first match. For a figure the model
                                #   reproduces to a tolerance; state the measurement it comes from beside it
      - {re: "\\(TRACE\\) 0x0800abcd ", count: true, min: 700, max: 800}  # `count: true`: the number of matches

Cellular fixtures may also assert on the run's activity report (see
`sim --activity-report`), which is what UART text cannot show: how long the
modem was actually RRC-connected, how often it re-registered, what woke it up.

    activity:
      rrc_connected_count: "<=2"     # comparison, or a bare value for equality
      reregistrations: 1
      totals.dns_lookups: ">=1"      # dotted keys are paths from the report root

Bare keys are looked up in the first report's `totals`; a key with a dot is a
path from that report's root. Values are a number/string for equality, or a
string starting with <=, >=, <, >, ==, != for a comparison.

`activity_deterministic: true` additionally runs the fixture a second time and
asserts the two reports agree: identical totals, identical state timelines, and
identical event sequences. Virtual timestamps are compared with a 1 ms
tolerance because the engine places a guest event with about a microsecond of
run-to-run jitter; everything the report is read for is exact.

Multi-machine fixtures declare a `machines:` map (and usually a shared `media:`
bus); each machine names its own mcu + elf. These run through `sim --scenario`:

    machines:
      central:    { mcu: NRF52840QIAA, elf: central.elf }
      peripheral: { mcu: NRF52840QIAA, elf: peripheral.elf }
      esp:        { mcu: ESP32-C3, flash: flash.bin, rom: qemu-espressif/pc-bios/esp32c3-rom.bin, mac: "24:0a:c4:00:00:a0" }
    media:
      - { type: ble, connect: [central.radio, peripheral.radio] }
    timeout: 30
    expect:        ["[central] RESULT: PASS"]
    expect_absent: ["RESULT: FAIL"]

A machine with `flash:` is a native model (ESP32-C3 ROM + flash image): `rom:` is
relative to the simantic checkout root, or env `ESP32C3_ROM` if that file is missing.

For each test we run sim on the served model(s) plus the optional board file, and
assert every `expect` substring is present and every `expect_absent` is absent.
The ONLY observable simantic-cli exposes is UART text, so tests are self-contained
firmware that print a PASS/FAIL marker over the console UART.

Usage:
    run_smoke.py <sim-binary> [--engine <name>] [--ci] [--wireless | --no-wireless] [--published] [--json <path>] [--work <dir>] [test-dir ...]
                                               # default: every tests/**/ with test.yaml

Tests run `sim <mcu> run --overlay <board>` for one machine, `mcu:` plus
`overlay:` per machine in a scenario. `--published` is accepted for compatibility and changes nothing.
Under `--engine pyrite` a run takes the model pyrite serves (a chip rn-chips
defines is built in; the rest are fetched), so results.json records
`"models": "cached"` and `cache_fetched` (per-mcu fetch time).

Engines. Manifests name `pyrite` (the Rust `sim`); `simantic` (the retired
Renode-hosted C# `sim`) survives only as a name in old `ci:`/`gate:` lists. `--engine` names the one under test
(default pyrite); it selects the `ci:` gate, the `xfail:` allowances and
the engine-specific strictness flags passed to the binary.

    gate: [simantic]         # engines whose PER-PUSH gate this fixture is in
    ci: [simantic, pyrite]   # engines whose CI gate this fixture is in (opt-in)
    xfail:                   # per-engine expected failure, with a reason
      pyrite: "pyrite#NNN: ESP32-P4 models not in the replx builder"

Three tiers, narrowest first. `--gate` runs one cheap boot fixture per MCU
family: an engine change is checked against every family we model, in a few
minutes. `--ci` runs the wider opt-in set, for manual and scheduled runs.
No flag runs everything, which is the pre-push gate and belongs on the
developer's machine -- macOS CI minutes cost ~10x Linux, so the hosted gate is
Linux-only and deliberately narrow. `--gate` reports any family with no gate
fixture, because a family the push tier cannot see is the failure it exists to
prevent.

`xfail` is two-sided: the fixture is still run, a failure reports XFAIL, and a
PASS reports XPASS and fails the whole run -- a fixed gap must be un-listed,
so a stale allowance cannot absorb the next failure to look like it.

`--skip-xfail` turns that off for one run: an already-listed fixture is not
executed at all (SKIP-XFAIL). It is meant for the per-push gate, where the
expected-failure set is most of the wall-clock time and none of the regression
detection. It trades away XPASS detection, so a run without the flag has to
happen periodically or a fixed gap goes unnoticed.

Every fixture ends in exactly one status, and only PASS counts as passing:
PASS, FAIL, XFAIL, XPASS, SKIP-HARDWARE (a `hardware: true` HIL-only manifest,
never run here, never counted as passed). Exit 0 only when no FAIL and no
XPASS. `--json <path>` writes every result with the firmware's SHA-256 and the
checkout SHAs, so a CI artifact records what ran, not what was asked for.
`--sim-args "<flags>"` appends flags verbatim to every `sim` invocation, so a
fixture can be profiled or traced without editing its test.yaml (which is where
`sim_args` lives, and which is a reviewed file). The fixture still resolves its
MCU, merges its overlay and checks expect/expect_absent, so a profiled run is
the same run that passes the gate. Such runs also echo sim's own
Elapsed/Resources lines, which are otherwise discarded.

    run_smoke.py $SIM tests/nrf/nrf52/ble-conn --sim-args \
        "--profile-dir /tmp/p --profile-format perfetto \
         --profile-start-symbol main --profile-window-ms 45"

`--work <dir>` keeps the run's scratch directory instead of a temp dir that is
deleted at exit: the merged `<name>.replx`, the generated `<name>.scenario.yaml`
and the captured `<name>.out.txt` stay there, ready for a hand run of the same
platform with trace flags (`sim --scenario <dir>/<name>.scenario.yaml ...`).

Wall clock is capped per fixture class, independently of `timeout`: 100 s for
wireless fixtures (a radio or coprocessor stack costs real host time) and 30 s
for everything else. `wireless: true|false` sets the class explicitly; without
it the class is inferred from any declared wireless `media:` and from the
fixture directory name. The cap is flat rather than `timeout + slack` so that a
model getting slower fails the gate instead of quietly eating headroom. A
fixture that cannot fit either class (a full flight stack booting twice) states
its own ceiling with `wall: <seconds>`. Such a fixture normally stays out of the
`ci:` gate; the exception is one whose cost is faithful work rather than a slow
model (P4 ota-recovery boots six times over a UART that now takes wire time,
and still runs 3x faster than real time), which says so next to its `wall:`.

Invoke via ./run-smoke.sh, which supplies pyyaml through uv.
"""
import concurrent.futures
import contextlib
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fat16 import write_fat16  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# sim stderr lines that mean the run is broken whatever the exit code says.
SIM_ERROR = re.compile(r"^(Error|error): |panicked at ")
GREEN, RED, YELLOW, DIM, RST = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

PASS, FAIL, XFAIL, XPASS, SKIP_HARDWARE = "PASS", "FAIL", "XFAIL", "XPASS", "SKIP-HARDWARE"
SKIP_XFAIL = "SKIP-XFAIL"
SKIP_NOELF = "SKIP-NOELF"

# Extra `sim` arguments per engine. pyrite's `--fail-on-skipped-models` makes
# a platform whose description names a model the engine has not ported a
# build error instead of a silently hollow machine.
ENGINES = {
    "simantic": [],
    "pyrite": ["--fail-on-skipped-models"],
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def link_external_files(test_dir: Path, files: dict) -> str | None:
    """Symlinks each `external_files` blob from the cache into test_dir; returns the first one
    absent from the cache. A blob whose hash differs from the manifest's is a hard error."""
    cache = Path(os.environ.get("SIM_FIXTURES_CACHE", Path.home() / ".cache" / "sim-fixtures"))
    for fname, sha in files.items():
        src = cache / sha
        if not src.exists():
            return fname
        dst = test_dir / fname
        if not (dst.is_symlink() and dst.resolve() == src.resolve()):
            if sha256_of(src) != sha:
                die(f"{src}: sha256 does not match {fname}'s {sha}")
            dst.unlink(missing_ok=True)
            dst.symlink_to(src)
    return None


def git_head(repo: Path) -> "str | None":
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def die(msg: str) -> "NoReturn":
    print(f"{RED}error:{RST} {msg}", file=sys.stderr)
    sys.exit(2)


def read_overlay(path: Path, test_dir: Path, work: Path) -> str:
    """Overlay fragment text with `{TEST_DIR}` expanded to its test directory
    and `{WORK_DIR}` to the run's scratch directory (see `sparse_files:`).

    Fragments that reference sidecar files (a ScriptedPeripheral's `file:`)
    need an absolute path: the merged model is written to a temp dir, so a
    relative path would resolve against that instead of the test.
    """
    text = path.read_text().replace("{TEST_DIR}", str(test_dir)).replace("{WORK_DIR}", str(work))
    return copy_images(text, work)


def copy_images(text: str, work: Path) -> str:
    """Point every `imageFile:` at a copy of it in the run's work dir.

    The SD card models write accepted blocks back to their image file, so a
    checked-in image would carry one run's writes (logs, a formatted volume)
    into the next run, and into every fixture sharing it: runs stop being
    reproducible and the checkout stops being pristine. A path that is not an
    existing file (absent: a zero-filled card) is left alone.
    """
    def copy(m):
        src = Path(m.group(2))
        if not src.is_absolute():
            src = ROOT / src
        if not src.is_file() or src.resolve().is_relative_to(work.resolve()):
            return m.group(0)
        # A file of its own: fixtures running in parallel share `work`, and often an image name.
        fd, dest = tempfile.mkstemp(dir=work, prefix="image-", suffix=f"-{src.name}")
        os.close(fd)
        shutil.copyfile(src, dest)
        return f"{m.group(1)}{dest}{m.group(3)}"

    return re.sub(r'(imageFile:\s*")([^"]+)(")', copy, text)


def parse_size(spec) -> int:
    """`4G`, `512M`, `64K` or a bare byte count."""
    if isinstance(spec, int):
        return spec
    m = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)\s*", str(spec), re.I)
    if not m:
        die(f"sparse_files: cannot parse size {spec!r}")
    return int(m.group(1)) << {"": 0, "K": 10, "M": 20, "G": 30, "T": 40}[m.group(2).upper()]


def create_sparse_files(meta: dict, work: Path) -> None:
    """Create the fixture's `sparse_files:` blank in the work dir.

    A block-device image the guest formats itself (an SD card PX4 runs
    mkfatfs on) cannot be committed at its nominal size, and must not persist
    between runs or the second run would boot on a formatted card the first
    one left behind. A sparse file costs no disk until written.
    """
    for name, size in (meta.get("sparse_files") or {}).items():
        path = work / str(name)
        with open(path, "wb") as f:
            f.truncate(parse_size(size))
    for name, size in (meta.get("fat16_files") or {}).items():
        write_fat16(work / str(name), parse_size(size))


SIM_ARGS: "list[str]" = []  # --sim-args, appended to every sim invocation
CACHED_MCUS: set = set()  # mcus a pyrite --published run took from ~/.sim_cache


def cache_fetched(mcu: str) -> "str | None":
    """When ~/.sim_cache/<mcu>.json was fetched (its mtime, UTC), or None if absent."""
    f = Path.home() / ".sim_cache" / f"{mcu.lower()}.json"
    return (datetime.datetime.fromtimestamp(f.stat().st_mtime, datetime.timezone.utc)
            .isoformat(timespec="seconds") if f.exists() else None)


def preload_specs(test_dir: Path, specs) -> list:
    """`preload:` entries as `sim` takes them (`FILE[@ADDR]`), with the file made absolute against the fixture."""
    out = []
    for spec in specs or []:
        file, at, addr = str(spec).partition("@")
        out.append(str((test_dir / file).resolve()) + (at + addr))
    return out


def board_file(overlay: "str | None", dest: Path) -> "Path | None":
    """The overlay with its {TEST_DIR}/{WORK_DIR} expanded, as a file sim can take."""
    if not overlay:
        return None
    dest.write_text(overlay)
    return dest


WALL_WIRELESS = 100
WALL_WIRED = 30


def wall_budget(meta: dict, test_dir: Path) -> int:
    """Wall-clock ceiling for one fixture, in seconds.

    This is deliberately a flat ceiling per class, not `timeout + slack`. The
    virtual timeout says how long the guest may run; it says nothing about how
    long the host may take to get there, and the two drifted far apart -- a
    fixture with a 90 s virtual timeout was allowed 180 s of wall clock, so a
    model that got 3x slower still passed. A flat ceiling turns that back into
    a failure, which is the point: slow simulation is a model gap, not a fact
    of life.

    Wireless fixtures get more because they carry a whole radio/coprocessor
    stack; everything else must finish in 30 s.
    """
    if "wall" in meta:
        return int(meta["wall"])
    return WALL_WIRELESS if is_wireless(meta, test_dir) else WALL_WIRED


WIRELESS_WORDS = {"hosted", "wifi", "ble", "ppp", "lte", "redcap", "openthread",
                  "zigbee", "ieee802154", "radio", "gnss", "espnow"}


def is_wireless(meta: dict, test_dir: Path) -> bool:
    """Whether a fixture carries a radio or modem stack. Decides its wall
    budget and which side of `--wireless` / `--no-wireless` it runs on.

    An explicit `wireless:` key wins. Otherwise it is what the fixture plainly
    is: a radio medium, or a radio word in its directory name. Whole words
    only: `two-thread` and `can-filter-index` are not radio fixtures.
    """
    if "wireless" in meta:
        return bool(meta["wireless"])
    for m in (meta.get("media") or []):
        if str(m.get("type", "")).lower() in ("ble", "wifi", "ieee802154", "lte"):
            return True
    return bool(WIRELESS_WORDS & set(re.split(r"[-_]", test_dir.name.lower())))


def invoke_sim(sim: Path, args: "list[str]", name: str, wall: int, log: Path = None) -> bool:
    """Run `sim <args>`; return True on a clean exit, else print why and False.
    With `log`, sim's stderr (peer ctx.info/warning lines) is written there."""
    env = {**os.environ, "DOTNET_ROOT": os.environ.get("DOTNET_ROOT", "/nonexistent")}
    try:
        proc = subprocess.run(
            [str(sim), *args, *SIM_ARGS],
            # cwd=ROOT is part of the fixture contract, not a convenience:
            # board file attributes (tlsCertificate, httpsDocument, ...)
            # are repo-root-relative and the model resolves them against the
            # sim process's working directory. Inheriting the caller's cwd
            # made that resolution depend on where run_smoke was launched
            # from -- fine locally (always the repo root), broken in CI.
            cwd=ROOT,
            env=env, check=True,
            stdout=subprocess.PIPE if SIM_ARGS or log else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=wall,
        )
        if log:
            log.write_bytes(proc.stderr + proc.stdout)  # --trace-memory / --trace-symbol write to stdout
        # A sim that reports an error but still exits 0 (or whose firmware
        # printed the markers before the platform broke) must not pass.
        errors = [l for l in proc.stderr.decode(errors="replace").splitlines()
                  if SIM_ERROR.search(l)]
        if errors:
            print(f"{RED}✗ {name}{RST}  (sim reported an error on a clean exit)")
            for l in errors:
                print(f"    {DIM}|{RST} {l}")
            return False
        if SIM_ARGS and proc.stdout:
            for line in proc.stdout.decode(errors="replace").splitlines():
                if line.startswith(("Elapsed:", "Resources:")):
                    print(f"    {DIM}{line}{RST}")
        return True
    except subprocess.TimeoutExpired:
        print(f"{RED}✗ {name}{RST}  (sim wall-clock timeout, budget {wall}s)")
        return False
    except subprocess.CalledProcessError as e:
        # One print, every line tagged: with parallel jobs a bare stderr dump
        # lands inside another fixture's UART listing and cannot be traced.
        lines = e.stderr.decode(errors="replace").splitlines()
        print("\n".join([f"{RED}✗ {name}{RST}  (sim exited {e.returncode})"]
                        + [f"    {DIM}{name} |{RST} {l}" for l in lines]))
        return False


def assert_bands(name: str, out: Path, bands: list) -> bool:
    """Check `expect_bands:` against captured UART: each `re`'s first match, its one group read as a
    number, must lie in [min, max]."""
    text = out.read_text(errors="replace") if out.exists() else ""
    bad = []
    for b in bands:
        if b.get("count"):  # the number of matches, not a captured figure (e.g. --trace-symbol hits)
            n = len(re.findall(b["re"], text))
            if not b["min"] <= n <= b["max"]:
                bad.append(f"{b['re']!r}: {n} matches outside [{b['min']}, {b['max']}]")
            continue
        m = re.search(b["re"], text)
        if not m:
            bad.append(f"{b['re']!r}: no match")
        elif not b["min"] <= float(m.group(1)) <= b["max"]:
            bad.append(f"{b['re']!r}: {m.group(1)} outside [{b['min']}, {b['max']}]")
    if bad:
        print(f"{RED}✗ {name}{RST}  (expect_bands)")
        for s in bad:
            print(f"    {DIM}{s}{RST}")
        return False
    print(f"{DIM}    bands: {len(bands)} number(s) in band{RST}")
    return True


def assert_output(name: str, out: Path, expect: list, expect_absent: list, note: str,
                  kind: str = None) -> bool:
    """Check captured UART against expect / expect_absent; print result.

    kind labels WHY green is good (see docs/fidelity-gaps.md): "negative"
    fixtures plant a firmware bug the sim must reproduce (✓ caught:), and
    "fidelity_gap" fixtures assert current unfaithful sim behaviour so they
    go red the day the gap is fixed (⚠ gap:)."""
    text = out.read_text(errors="replace") if out.exists() else ""
    missing = [s for s in expect if s not in text]
    present = [s for s in expect_absent if s in text]
    if missing or present:
        print(f"{RED}✗ {name}{RST}")
        for s in missing:
            print(f"    {DIM}expected but missing:{RST} {s!r}")
        for s in present:
            print(f"    {DIM}forbidden but present:{RST} {s!r}")
        print(f"    {DIM}--- captured UART ---{RST}")
        for line in text.splitlines():
            print(f"    {DIM}|{RST} {line}")
        return False
    if kind == "negative":
        print(f"{GREEN}✓ caught: {name}{RST}  {DIM}({note}){RST}")
    elif kind == "fidelity_gap":
        print(f"{YELLOW}⚠ gap: {name}{RST}  {DIM}({note}){RST}")
    else:
        print(f"{GREEN}✓ {name}{RST}  {DIM}({note}){RST}")
    return True


def run_wire_check(sim: Path, test_dir: Path, name: str, spec, work: Path) -> bool:
    """Byte-level wire conformance, run after the UART assertions pass.

    UART assertions are structurally blind to a sim that is more permissive
    than silicon. The firmware is not malfunctioning — it genuinely completed
    the operation, so it prints success, truthfully. The divergence is in what
    the *environment* declined to object to, and the firmware has no view of
    that. This is the check that reads the bytes instead.

    `known_failures` is a tracking mechanism, not a mute button:

      - a failure matching no declared pattern fails the test, and
      - a declared pattern that matches nothing ALSO fails the test.

    The second half is the important one. It means a closed defect cannot
    leave a stale allowance behind that would silently absorb the next
    failure to look like it.
    """
    spec = spec if isinstance(spec, dict) else {}
    known = spec.get("known_failures") or []
    expect_mqtt = spec.get("expect_mqtt") or []
    expect_https = spec.get("expect_https") or []
    report = work / f"{name}.wire.json"
    # The checker resolves a fixture by directory name and drives the sim
    # itself, so this is a second run of the firmware — deliberate, to keep
    # the wire capture (which needs --show-renode-logs) out of the UART path.
    # Launcher: `uv run` locally (resolves scapy without polluting the
    # system env), but plain interpreter where uv doesn't exist (CI
    # installs scapy directly) — same script, same checks either way.
    if shutil.which("uv"):
        launcher = ["uv", "run"]
    else:
        launcher = [sys.executable]
    proc = subprocess.run(
        launcher + [str(ROOT / "scripts" / "check_packet_conformance.py"),
         str(sim), test_dir.name, "--json", str(report)],
        cwd=ROOT, capture_output=True, text=True, timeout=900)
    if not report.exists():
        print(f"{RED}✗ {name}{RST}  (wire check produced no report — it died "
              f"before checking anything)")
        sys.stderr.write(proc.stdout + proc.stderr)
        return False

    data = json.loads(report.read_text())
    failures = data["failures"]
    unexpected = [f for f in failures if not any(k in f for k in known)]
    stale = [k for k in known if not any(k in f for f in failures)]

    # Server-side telemetry check: what the modeled broker actually received,
    # decoded from the captured TCP stream. This is deliberately not the
    # firmware's own report — a device cannot vouch for its own payload, and
    # an echo back to the publisher only proves the broker can send.
    got = [(m["topic"], m["payload"]) for m in data.get("mqtt_publishes", [])]
    missing_mqtt = [e for e in expect_mqtt
                    if (e["topic"], e["payload"]) not in got]

    # Server-side telemetry check for HTTPS: what the modeled TLS-terminated
    # HTTPS server actually decrypted, the HTTPS analogue of missing_mqtt
    # above. TLS makes the wire opaque, so this is the only place payload
    # truth can come from.
    got_https = [(p["path"], p["body"]) for p in data.get("https_posts", [])]
    missing_https = [e for e in expect_https
                     if (e["path"], e["body"]) not in got_https]

    if unexpected or stale or missing_mqtt or missing_https:
        print(f"{RED}✗ {name}{RST}  (wire conformance)")
        for f in unexpected:
            print(f"    {DIM}unexpected:{RST} {f}")
        for k in stale:
            print(f"    {DIM}declared known_failure no longer occurs — remove "
                  f"it from test.yaml:{RST} {k!r}")
        for e in missing_mqtt:
            print(f"    {DIM}broker never received:{RST} "
                  f"topic={e['topic']!r} payload={e['payload']!r}")
        if missing_mqtt and got:
            print(f"    {DIM}broker did receive:{RST}")
            for t, pl in got:
                print(f"      topic={t!r} payload={pl!r}")
        for e in missing_https:
            print(f"    {DIM}https server never received:{RST} "
                  f"path={e['path']!r} body={e['body']!r}")
        if missing_https and got_https:
            print(f"    {DIM}https server did receive:{RST}")
            for p, b in got_https:
                print(f"      path={p!r} body={b!r}")
        return False

    note = f"{data['frames']} frames ({data['rx']} rx) wire-conformant"
    if expect_mqtt:
        note += f", {len(expect_mqtt)} telemetry publish(es) confirmed at the broker"
    if expect_https:
        note += f", {len(expect_https)} telemetry POST(s) confirmed at the server"
    if failures:
        note += f", {len(failures)} known-defect failure(s) allowed"
    print(f"{GREEN}✓ {name}{RST}  {DIM}({note}){RST}")
    return True


def _lookup(report: dict, key: str):
    """Resolve an `activity:` key against one report (see the module docstring)."""
    node = report if "." in key else report.get("totals", {})
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(key)
        node = node[part]
    return node


def assert_activity(name: str, report_path: Path, spec: dict) -> bool:
    """Check `activity:` constraints against the run's activity report."""
    if not report_path.exists():
        print(f"{RED}✗ {name}{RST}  (no activity report written: {report_path})")
        return False
    reports = json.loads(report_path.read_text()).get("reports", [])
    if not reports:
        print(f"{RED}✗ {name}{RST}  (activity report has no modem)")
        return False

    failures = []
    for key, want in spec.items():
        try:
            got = _lookup(reports[0], key)
        except KeyError:
            failures.append(f"{key}: not in the report")
            continue
        text = str(want).strip()
        for op in ("<=", ">=", "==", "!=", "<", ">"):
            if text.startswith(op):
                bound = float(text[len(op):])
                ok = {"<=": got <= bound, ">=": got >= bound, "==": got == bound,
                      "!=": got != bound, "<": got < bound, ">": got > bound}[op]
                if not ok:
                    failures.append(f"{key}: got {got}, want {op} {text[len(op):].strip()}")
                break
        else:
            if got != want:
                failures.append(f"{key}: got {got!r}, want {want!r}")

    if failures:
        print(f"{RED}✗ {name}{RST}  (activity report)")
        for f in failures:
            print(f"    {DIM}{f}{RST}")
        return False
    print(f"{DIM}    activity: {len(spec)} assertion(s) held{RST}")
    return True


def assert_deterministic(sim: Path, args: "list[str]", name: str, wall: int,
                         report_path: Path, work: Path) -> bool:
    """Run the fixture a second time and assert the activity report repeats.

    Compared structurally rather than byte for byte: the engine places a guest
    event with roughly a microsecond of run-to-run jitter, so raw timestamps
    wobble in their last digit while every quantity the report is actually read
    for -- counts, bytes, the sequence of states and events, and each state's
    duration -- is reproducible.
    """
    second = work / f"{name}.activity2.json"
    rerun = [str(second) if a == str(report_path) else a for a in args]
    if not invoke_sim(sim, rerun, name, wall):
        return False

    a = json.loads(report_path.read_text())["reports"][0]
    b = json.loads(second.read_text())["reports"][0]
    failures = []

    for key, want in a["totals"].items():
        got = b["totals"][key]
        if isinstance(want, float) or isinstance(got, float):
            if abs(got - want) > 1e-3:
                failures.append(f"totals.{key}: {want} then {got}")
        elif got != want:
            failures.append(f"totals.{key}: {want} then {got}")

    for machine_name, segments in a["timeline"].items():
        other = b["timeline"][machine_name]
        if [s["state"] for s in segments] != [s["state"] for s in other]:
            failures.append(f"timeline.{machine_name}: state sequence differs")
            continue
        for x, y in zip(segments, other):
            if abs(x["duration_s"] - y["duration_s"]) > 1e-3:
                failures.append(
                    f"timeline.{machine_name} {x['state']}: "
                    f"{x['duration_s']}s then {y['duration_s']}s")

    if [e["kind"] for e in a["events"]] != [e["kind"] for e in b["events"]]:
        failures.append("events: sequence differs")

    if failures:
        print(f"{RED}✗ {name}{RST}  (activity report is not reproducible)")
        for f in failures:
            print(f"    {DIM}{f}{RST}")
        return False
    print(f"{DIM}    activity: reproduced on a second run{RST}")
    return True


def run_test(sim: Path, test_dir: Path, work: Path, engine: str,
             skip_xfail: bool = False) -> dict:
    """One fixture -> a result record with exactly one `status`.

    The pass/fail machinery lives in `run_fixture`; this wrapper turns its
    bool into the status the manifest's `xfail:` and `hardware:` call for,
    and records what ran (firmware digest, wall time) for `--json`.
    """
    meta = yaml.safe_load((test_dir / "test.yaml").read_text()) or {}
    name = f"{test_dir.parent.name}-{test_dir.name}"
    record = {"name": name, "dir": str(test_dir.relative_to(ROOT)),
              "mcu": meta.get("mcu") or sorted(
                  {m.get("mcu") for m in (meta.get("machines") or {}).values()}),
              "engine": engine, "status": None, "reason": None,
              "elf_sha256": None, "duration_s": None}

    # HIL-only fixtures (`hardware: true`, no `mcu`/`machines`) have no sim
    # platform to resolve at all -- they document a manual real-board run.
    # Nothing ran, so nothing passed: counted apart, never as a PASS.
    if meta.get("hardware") and "mcu" not in meta and "machines" not in meta:
        print(f"{DIM}- {name}  (hardware-only, no sim run){RST}")
        record["status"] = SKIP_HARDWARE
        return record

    # `elf_external: true` marks a fixture whose ELF is a third-party build we
    # deliberately do not vendor (see the fixture's own .gitignore). CI checks
    # the repo out and builds nothing, so the binary is simply absent there --
    # skip distinctly rather than fail, the way hardware-only manifests do.
    # Without the opt-in a missing ELF stays a hard error, so "forgot to build"
    # still fails locally.
    elfs = [m.get("elf", "") for m in (meta.get("machines") or {}).values()] or (meta.get("elfs") or [meta.get("elf", "")])
    if meta.get("elf_external") and not all(e and (test_dir / e).exists() for e in elfs):
        print(f"{DIM}- {name}  (elf not vendored, no sim run){RST}")
        record["status"] = SKIP_NOELF
        return record

    missing = link_external_files(test_dir, meta.get("external_files") or {})
    if missing:
        print(f"{DIM}- {name}  (external file not in cache: {missing}, no sim run){RST}")
        record["status"] = SKIP_NOELF
        return record

    xfail = (meta.get("xfail") or {}).get(engine) or meta.get(f"xfail_{engine}")

    # --skip-xfail: don't spend the per-push run on fixtures that are already
    # known to fail for this engine. An xfail cannot fail the build, so running
    # it buys no regression detection -- and it is the expensive half: the ones
    # that fail by timing out each burn the full wall ceiling. The allowance
    # stays in test.yaml either way; this only decides whether it executes now.
    #
    # The cost is XPASS detection -- "a known-broken fixture started working"
    # is invisible while this is on. That is a question for a periodic full run
    # (drop the flag), not for every push.
    if skip_xfail and xfail:
        print(f"{DIM}- {name}  (xfail for {engine}, not run){RST}")
        record["status"] = SKIP_XFAIL
        record["reason"] = str(xfail)
        return record

    started = time.monotonic()
    ok = run_fixture(sim, test_dir, work, meta, name, engine, record)
    record["duration_s"] = round(time.monotonic() - started, 3)
    if xfail:
        record["reason"] = str(xfail)
        if ok:
            print(f"{RED}✗ XPASS: {name}{RST}  {DIM}(passed, but xfail says: {xfail} -- "
                  f"remove the allowance from test.yaml){RST}")
            record["status"] = XPASS
        else:
            print(f"{YELLOW}~ xfail: {name}{RST}  {DIM}({xfail}){RST}")
            record["status"] = XFAIL
    else:
        record["status"] = PASS if ok else FAIL
    return record


def run_pytest(sim: Path, test_dir: Path, test_file: str, name: str, wall: int) -> bool:
    """A fixture whose host side is a pytest file (`pytest: test_x.py`): the
    test drives `sim` itself (e.g. as a raw USB host), so pass = pytest exits 0.

    cwd is the fixture dir, not ROOT: the repo's `pytest/` directory would
    shadow the pytest package under `python -m pytest`. No cache or bytecode
    is written, so the checkout stays pristine for the CI check.
    """
    if importlib.util.find_spec("pytest") is None:
        print(f"{RED}✗ {name}{RST}  (pytest not installed for {sys.executable}; use the repo .venv)")
        return False
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test_file, "--sim", str(sim.resolve()),
             "-q", "-rs", "-p", "no:cacheprovider"],
            cwd=test_dir, env=env, capture_output=True, text=True, timeout=wall)
    except subprocess.TimeoutExpired:
        print(f"{RED}✗ {name}{RST}  (pytest wall-clock timeout, budget {wall}s)")
        return False
    if proc.returncode != 0:
        print(f"{RED}✗ {name}{RST}  (pytest exited {proc.returncode})")
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-2000:])
        return False
    return True


def run_fixture(sim: Path, test_dir: Path, work: Path, meta: dict,
                name: str, engine: str, record: dict) -> bool:
    timeout = float(meta.get("timeout", 15))
    timeout = int(timeout) if timeout.is_integer() else timeout  # sub-second: ns-scale pad_edges runs
    wall = wall_budget(meta, test_dir)
    expect = meta.get("expect", []) or []
    expect_absent = meta.get("expect_absent", []) or []
    out = work / f"{name}.out.txt"

    if meta.get("pytest"):
        return run_pytest(sim, test_dir, meta["pytest"], name, wall)

    # A manifest with nothing to expect passes on empty output: a sim that
    # exits 0 and writes nothing satisfies `expect_absent` vacuously. Refuse
    # it here rather than let a fixture exist that cannot fail.
    if not expect and not meta.get("pad_edges") and not meta.get("expect_log"):
        die(f"{test_dir.relative_to(ROOT)}/test.yaml has no `expect:` or `expect_log:` strings -- "
            f"a fixture that expects nothing cannot fail, so it proves nothing")

    engine_args = list(ENGINES[engine])
    if engine == "pyrite":
        CACHED_MCUS.update(m["mcu"] for m in (meta.get("machines") or {"dut": meta}).values() if m.get("mcu"))
    create_sparse_files(meta, work)

    # pyrite has no single-machine `--mcu --board`: a one-machine fixture runs
    # as a one-machine scenario (machine `dut`), with `--usb-cdc-console` etc.
    # carried over from `sim_args`.
    gpio_in = "--gpio-in" in (meta.get("sim_args") or [])  # single-plain-run only, like pad_edges
    defmt = meta.get("defmt")  # single-plain-run only: --rtt file: is per machine
    rtt_text = meta.get("rtt")  # the same, kept as text
    wired = "--wire" in (meta.get("sim_args") or [])  # SET_F loopback wires: `sim run --wire` only, no scenario form
    if engine == "pyrite" and "machines" not in meta and not meta.get("pad_edges") and not gpio_in and not defmt and not rtt_text and not wired:
        dut = {k: meta[k] for k in ("mcu", "overlay", "elf", "elfs", "parts", "rom", "preload") if k in meta}
        if "elfs" not in dut:
            dut.setdefault("elf", "nucleo_f401re.elf")
        meta = {**meta, "machines": {"dut": dut}}
    # Multi-machine fixtures (a `machines:` map) run through `sim --scenario`.
    if "machines" in meta:
        return run_scenario_test(sim, test_dir, work, meta, name,
                                 timeout, expect, expect_absent, out, engine_args,
                                 record)

    overlay = read_overlay(test_dir / meta["overlay"], test_dir, work) if meta.get("overlay") else None
    board = board_file(overlay, work / f"{name}.board")
    platform = [meta["mcu"], "run"] + (["--overlay", str(board)] if board else [])

    elf = test_dir / meta.get("elf", "nucleo_f401re.elf")
    if not elf.exists():
        print(f"{RED}✗ {name}{RST}  (elf missing: {elf.name} — build with "
              f"./scripts/setup/zephyr.sh --build {test_dir.relative_to(ROOT)})")
        return False

    record["elf_sha256"] = sha256_of(elf)
    args = platform + ["--elf", str(elf), "--ascii",
            "--only-messages", "--output", str(out), "--timeout", str(timeout)]
    for spec in preload_specs(test_dir, meta.get("preload")):
        args += ["--preload", spec]
    activity = meta.get("activity") or {}
    activity_path = work / f"{name}.activity.json"
    if activity:
        args += ["--activity-report", str(activity_path)]
    pad = meta.get("pad")
    pad_route = meta.get("pad_route")
    pad_edges = meta.get("pad_edges")
    pad_trace = work / f"{name}.pad.jsonl"
    if pad or pad_route or pad_edges:
        args += ["--trace-out", str(pad_trace)]
    if meta.get("trace_interrupts") not in (None, "balanced"):
        print(f"{RED}✗ {name}{RST}  (trace_interrupts: {meta['trace_interrupts']!r}; only 'balanced' is supported)")
        return False
    if meta.get("trace_interrupts") == "balanced":
        args.append("--trace-interrupts")
    args += [str(a) for a in (meta.get("sim_args") or [])]
    args += engine_args
    if meta.get("negative") and "--fail-on-skipped-models" in engine_args:
        args += ["--on-fault", "continue"]  # pyrite fails a faulted run; here the planted bug may fault and the markers judge it
    rtt = work / f"{name}.rtt"
    if defmt or rtt_text:
        args += ["--rtt", f"file:{rtt}"]
    expect_log = meta.get("expect_log") or []
    log_path = work / f"{name}.log.txt"
    if not invoke_sim(sim, args, name, wall, log_path if expect_log else None):
        return False
    if expect_log and not assert_output(name, log_path, expect_log, [], "sim log"):
        return False
    if defmt:
        if not shutil.which("defmt-print"):
            print(f"{RED}✗ {name}{RST}  (defmt: true needs defmt-print: cargo install defmt-print)")
            return False
        log = subprocess.run(["defmt-print", "-e", str(elf)], stdin=rtt.open("rb"),
                             capture_output=True, text=True).stdout
        with out.open("a") as f:
            f.write(log)
    if rtt_text and rtt.exists():
        with out.open("a") as f:
            f.write(rtt.read_text(errors="replace"))
    kind = ("negative" if meta.get("negative") else
            "fidelity_gap" if meta.get("fidelity_gap") else None)
    if not assert_output(name, out, expect, expect_absent,
                         f"{meta['mcu']}, {len(expect)} marker(s)", kind=kind):
        return False
    if meta.get("expect_bands") and not assert_bands(name, out, meta["expect_bands"]):
        return False
    if meta.get("trace_interrupts") == "balanced":
        why = trace_balance_failure(out.read_text(errors="replace"))
        if why:
            print(f"{RED}✗ {name}{RST}  (interrupt trace {why})")
            return False
    if activity and not assert_activity(name, activity_path, activity):
        return False
    if meta.get("activity_deterministic") and not assert_deterministic(
            sim, args, name, wall, activity_path, work):
        return False
    if pad and not assert_pad(name, pad_trace, pad, work):
        return False
    if pad_edges and not assert_pad_edges(name, pad_trace, pad_edges, work):
        return False
    if pad_route and not assert_pad_route(name, pad_trace, pad_route, work):
        return False
    if meta.get("wire"):
        return run_wire_check(sim, test_dir, name, meta["wire"], work)
    return True


def trace_balance_failure(text: str, max_open: int = 2) -> "str | None":
    """Check `--trace-interrupts` lines ("(IRQ) <trap> enter|exit", timestamp optional).

    Per name, entries must equal exits, except up to `max_open` frames still
    open at the end (at most one per name), and up to `max_open` `level N`
    orphan exits anywhere.
    `max_open` is one per CPU (2 on the S3); trace lines carry no CPU tag.
    FreeRTOS starts each core's first task with an rfe that has no entry.
    Returns a failure message, or None when balanced."""
    n: "dict[str, int]" = {}
    orphans = 0
    for m in re.finditer(r"^(?:\[[^\]]*\]\s*)?\(IRQ\) (.+) (enter|exit)\s*$", text, re.M):
        trap, edge = m.groups()
        if edge == "exit" and re.fullmatch(r"level \d+", trap):
            orphans += 1
            continue
        n[trap] = n.get(trap, 0) + (1 if edge == "enter" else -1)
    if orphans > max_open:
        return f"{orphans} orphan 'level N' exits (allowed {max_open})"
    bad = {k: v for k, v in n.items() if v}
    if any(v < 0 or v > 1 for v in bad.values()) or sum(bad.values()) > max_open:
        return "unbalanced (enter-exit): " + ", ".join(
            f"{k}={v:+d}" for k, v in list(bad.items())[:3])
    return None


def assert_pad(name: str, trace: Path, spec: dict, work: Path) -> bool:
    """Check `pad:` against the run's GPIO trace (see check_pad_timing.py).

    UART markers say what the firmware concluded; this reads the pad the
    peripheral drove. The expected numbers are what the registers the driver
    programmed dictate, so a pass means the model's waveform is the one the
    silicon produces for that programme, whatever the firmware timed.
    """
    report = work / f"{name}.pad.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_pad_timing.py"), str(trace),
         spec.get("port", "gpio"), str(spec["pin"]), json.dumps(spec["expect"]),
         "--json", str(report)],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        print(f"{DIM}    pad: {len(spec['expect'])} case(s) matched on gpio{spec['pin']}{RST}")
        return True
    print(f"{RED}✗ {name}{RST}  (pad waveform on gpio{spec['pin']})")
    sys.stderr.write(proc.stdout + proc.stderr)
    return False


def assert_pad_edges(name: str, trace: Path, spec: dict, work: Path) -> bool:
    """Check `pad_edges:` against the run's GPIO trace (see check_pad_edges.py).

    Nanosecond-resolution edge timing of one pad, for timer/PWM fixtures whose
    firmware prints nothing. Expected numbers come from the registers the
    firmware wrote, never from the firmware's own timing.
    """
    for i, one in enumerate(spec if isinstance(spec, list) else [spec]):
        report = work / f"{name}.pad_edges{i}.json"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_pad_edges.py"), str(trace),
             json.dumps(one), "--json", str(report)],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            print(f"{RED}✗ {name}{RST}  (pad edges)")
            sys.stderr.write(proc.stdout + proc.stderr)
            return False
        print(f"{DIM}    pad_edges: {proc.stdout.strip()}{RST}")
    return True


def assert_pad_route(name: str, trace: Path, spec: dict, work: Path) -> bool:
    """Check `pad_route:` (which pads carry what, in order) against the run's
    GPIO trace; see scripts/check_pad_route.py."""
    report = work / f"{name}.pad_route.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_pad_route.py"), str(trace),
         spec.get("port", "gpio"), json.dumps(spec), "--json", str(report)],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        print(f"{DIM}    pad_route: {len(spec['states'])} state(s) in order on "
              f"gpio{'/'.join(str(p) for p in spec['pins'])}{RST}")
        return True
    print(f"{RED}✗ {name}{RST}  (pad routing)")
    sys.stderr.write(proc.stdout + proc.stderr)
    return False


def resolve_native(mname: str, mspec: dict, test_dir: Path) -> "dict | None":
    """Scenario entry for a native machine (`flash:`). `rom:` is relative to this
    repo, then to the simantic checkout root (its parent), with env ESP32C3_ROM as the
    fallback for checkouts that do not have it; None if neither exists."""
    flash = (test_dir / mspec["flash"]).resolve()
    rel = mspec.get("rom") or ""
    rom = ROOT / rel if rel else None  # third-party/esp-rom/<chip>_rom.elf
    if rel and not rom.exists():
        rom = ROOT.parent / rel
    if rel and not rom.exists():
        # worktree under <simantic>/.worktrees/: the checkout root is one level up
        rom = ROOT.parent.parent / rel
    if rom is None or not rom.exists():
        env_rom = os.environ.get("ESP32C3_ROM")
        if env_rom and Path(env_rom).exists():
            rom = Path(env_rom)
        else:
            print(f"{RED}rom for '{mname}' not found at {ROOT.parent / rel}; "
                  f"set ESP32C3_ROM to the mask ROM path{RST}")
            return None
    entry = {"mcu": mspec["mcu"], "flash": str(flash), "rom": str(rom.resolve())}
    if mspec.get("mac") is not None:
        entry["mac"] = mspec["mac"]
    return entry


def run_scenario_test(sim: Path, test_dir: Path, work: Path,
                      meta: dict, name: str, timeout: int,
                      expect: list, expect_absent: list, out: Path,
                      engine_args: list, record: dict) -> bool:
    """Emit a scenario YAML naming each machine's served model, and run it
    through `sim --scenario` (the multi-machine entry point)."""
    machines = meta["machines"]
    scenario_machines = {}
    shas = {}
    for mname, mspec in machines.items():
        mcu = mspec.get("mcu")
        if not mcu:
            print(f"{RED}✗ {name}{RST}  (machine '{mname}' has no mcu)")
            return False
        if mspec.get("flash"):
            # Native machine: the image boots on the engine's own model (ESP32-C3
            # ROM + flash), so there is no repl or elf to resolve.
            native = resolve_native(mname, mspec, test_dir)
            if native is None:
                print(f"{RED}✗ {name}{RST}  (machine '{mname}' rom unresolved)")
                return False
            scenario_machines[mname] = native
            continue
        overlay = read_overlay(test_dir / mspec["overlay"], test_dir, work) if mspec.get("overlay") else None
        board = board_file(overlay, work / f"{name}-{mname}.board")
        platform = {"mcu": mcu, **({"overlay": str(board)} if board else {})}
        elfs = [test_dir / e for e in (mspec.get("elfs") or [mspec.get("elf", "")])]  # `elfs:` is one image per core
        if not (mspec.get("elfs") or mspec.get("elf")) or not all(e.exists() for e in elfs):
            print(f"{RED}✗ {name}{RST}  (machine '{mname}' elf missing: {mspec.get('elfs') or mspec.get('elf')!r})")
            return False
        elf = elfs[0]
        key, value = ("elfs", [str(e.resolve()) for e in elfs]) if mspec.get("elfs") else ("elf", str(elf.resolve()))
        scenario_machines[mname] = {**platform, key: value}
        if mspec.get("rom"):  # ESP32 / ESP32-S3 mask ROM for a raw flash image, test-dir relative
            scenario_machines[mname]["rom"] = str((test_dir / mspec["rom"]).resolve())
        if mspec.get("mac") is not None:  # efuse MAC, as for native `flash:` machines
            scenario_machines[mname]["mac"] = mspec["mac"]
        if mspec.get("parts") and "--fail-on-skipped-models" in engine_args:  # pyrite only
            scenario_machines[mname]["parts"] = mspec["parts"]
        if mspec.get("clockPpm") is not None:
            scenario_machines[mname]["clockPpm"] = mspec["clockPpm"]
        if mspec.get("preload"):
            scenario_machines[mname]["preload"] = preload_specs(test_dir, mspec["preload"])
        if mspec.get("analog"):  # ADC input voltages over virtual time, passed through as written
            scenario_machines[mname]["analog"] = mspec["analog"]
        if mspec.get("parts"):
            # Script/image paths are relative to the fixture, not the generated scenario.
            scenario_machines[mname]["parts"] = [
                {k: str((test_dir / v).resolve()) if k in ("script", "image") else v for k, v in part.items()}
                for part in mspec["parts"]]
        shas[mname] = [sha256_of(e) for e in elfs] if len(elfs) > 1 else sha256_of(elf)
    record["elf_sha256"] = shas

    scenario = {
        "machines": scenario_machines,
        "media": meta.get("media", []) or [],
        "networkServices": meta.get("networkServices", []) or [],
        "timeout": timeout,
    }
    for medium in scenario["media"]:  # A replay file is relative to the fixture, not the generated scenario.
        gateway = medium.get("gateway") or {}
        if gateway.get("replay"):
            gateway["replay"] = str((test_dir / gateway["replay"]).resolve())
    if meta.get("quantum") is not None:
        scenario["quantum"] = meta["quantum"]
    if meta.get("nets"):
        scenario["nets"] = meta["nets"]
    # Passed through as written: the seed every probabilistic impairment draws
    # from, and serial execution for runs that must interleave the same way twice.
    for key in ("seed", "serialExecution"):
        if meta.get(key) is not None:
            scenario[key] = meta[key]

    wall = wall_budget(meta, test_dir)
    if "--fail-on-skipped-models" in engine_args:  # pyrite gate run: 2x the stated wall, else 300 s
        wall = 2 * int(meta.get("wall", 150))

    scen_path = work / f"{name}.scenario.yaml"
    scen_path.write_text(yaml.safe_dump(scenario, sort_keys=False))

    args = ["run", "--scenario", str(scen_path), "--ascii", "--only-messages",
            "--output", str(out), "--timeout", str(timeout)]
    activity = meta.get("activity") or {}
    activity_path = work / f"{name}.activity.json"
    if activity:
        args += ["--activity-report", str(activity_path)]
    # `expect_frames` / `expect_frames_absent` match the --frames record file:
    # one line per Tx/Rx/Dropped/Corrupted frame with its timestamp and reason,
    # which is where a medium's drops, corruptions and collisions are reported.
    expect_frames = meta.get("expect_frames") or []
    expect_frames_absent = meta.get("expect_frames_absent") or []
    frames_path = work / f"{name}.frames.txt"
    if expect_frames or expect_frames_absent:
        args += ["--frames", str(frames_path)]
    pad = meta.get("pad")  # scenario runs check these too, else the fixture asserts nothing
    pad_edges = meta.get("pad_edges")
    pad_trace = work / f"{name}.pad.jsonl"
    if pad or pad_edges:
        args += ["--trace-out", str(pad_trace)]
    if "--fail-on-skipped-models" in engine_args:
        args += [str(a) for a in (meta.get("sim_args") or [])]
    args += engine_args
    if meta.get("negative") and "--fail-on-skipped-models" in engine_args:
        args += ["--on-fault", "continue"]  # pyrite fails a faulted run; here the planted bug may fault and the markers judge it
    expect_log = meta.get("expect_log") or []
    expect_log_absent = meta.get("expect_log_absent") or []
    log_path = work / f"{name}.log.txt"
    if not invoke_sim(sim, args, name, wall, log_path if expect_log or expect_log_absent else None):
        return False
    if not assert_output(name, out, expect, expect_absent,
                         f"{len(scenario_machines)} machines via --scenario",
                         kind=("negative" if meta.get("negative") else
                               "fidelity_gap" if meta.get("fidelity_gap") else None)):
        return False
    if meta.get("expect_bands") and not assert_bands(name, out, meta["expect_bands"]):
        return False
    if (expect_frames or expect_frames_absent) and not assert_output(
            name, frames_path, expect_frames, expect_frames_absent, "frames"):
        return False
    if pad and not assert_pad(name, pad_trace, pad, work):
        return False
    if (expect_log or expect_log_absent) and not assert_output(
            name, log_path, expect_log, expect_log_absent, "peer log"):
        return False
    if pad_edges and not assert_pad_edges(name, pad_trace, pad_edges, work):
        return False
    if activity and not assert_activity(name, activity_path, activity):
        return False
    if meta.get("activity_deterministic") and not assert_deterministic(
            sim, args, name, wall, activity_path, work):
        return False
    return True


def ci_engines(meta: dict, test_dir: Path) -> list:
    """The engines whose CI gate this fixture is in. A list of engine names;
    the pre-engine `ci: true` form is refused so the gate set is explicit."""
    ci = meta.get("ci")
    if ci is None or ci is False:
        return []
    if isinstance(ci, list) and all(isinstance(e, str) for e in ci):
        unknown = [e for e in ci if e not in ENGINES]
        if unknown:
            die(f"{test_dir.relative_to(ROOT)}/test.yaml: `ci:` names unknown "
                f"engine(s) {unknown}; known: {sorted(ENGINES)}")
        return ci
    die(f"{test_dir.relative_to(ROOT)}/test.yaml: `ci:` must be a list of engines "
        f"(e.g. `ci: [simantic]`), got {ci!r}")


def gate_engines(meta: dict, test_dir: Path) -> list:
    """The engines whose per-push gate this fixture is in (`gate: [<engine>,
    ...]`). Same shape as `ci:`, one tier narrower: `gate:` is the handful of
    cheap boot smokes that run on every engine change; `ci:` is the wider set."""
    gate = meta.get("gate")
    if gate is None or gate is False:
        return []
    if isinstance(gate, list) and all(isinstance(e, str) for e in gate):
        unknown = [e for e in gate if e not in ENGINES]
        if unknown:
            die(f"{test_dir.relative_to(ROOT)}/test.yaml: `gate:` names unknown "
                f"engine(s) {unknown}; known: {sorted(ENGINES)}")
        return gate
    die(f"{test_dir.relative_to(ROOT)}/test.yaml: `gate:` must be a list of engines "
        f"(e.g. `gate: [simantic]`), got {gate!r}")


def family_of(test_dir: Path) -> str:
    """`tests/<vendor>/<board>/<fixture>` -> `<vendor>/<board>`: the unit the
    per-push gate is meant to cover exactly once."""
    rel = test_dir.relative_to(ROOT / "tests").parts
    return "/".join(rel[:2]) if len(rel) >= 2 else rel[0]


# `mcu:` -> the CPU architectures the engine runs for it, first match wins. A CPU
# change in pyrite selects the fixtures of its architecture (pyrite ci/path-tags.yml).
ARCH_BY_MCU = [
    (r"ESP32-(C|P4|H)", {"rv32"}),
    (r"ESP32", {"xtensa"}),
    (r"RP2350", {"arm-m", "rv32"}),  # Cortex-M33 or Hazard3, per firmware
    (r"AM6442", {"arm-a64", "arm-r", "arm-m"}),  # the whole die: A53 + R5F + M4F
    (r"AM2|AM64x-R5F|mps3-an536", {"arm-r"}),
    (r"SG2002", {"rv64"}),
    (r"STM32|NUCLEO-|NRF|MIMXRT|MIMX93|Teensy|S32K|RP2040|RPI-PICO|Pixhawk|Ulysses", {"arm-m"}),
]
ALL_ARCHES = {"arm-m", "arm-r", "arm-a64", "rv32", "rv64", "xtensa"}


def fixture_arches(meta: dict) -> set:
    """arch:<a> for every machine's `mcu:`. An unknown or missing `mcu:` carries
    every arch, so a CPU change never skips a fixture nobody mapped."""
    machines = (meta.get("machines") or {}).values() or [meta]
    out = set()
    for m in machines:
        mcu = str((m or {}).get("mcu") or meta.get("mcu") or "")
        out |= next((a for pat, a in ARCH_BY_MCU if re.match(pat, mcu)), ALL_ARCHES)
    return {f"arch:{a}" for a in out}


def fixture_tags(meta: dict, test_dir: Path) -> set:
    """Explicit `tags:` from test.yaml plus implicit ones derived from the path
    and manifest, so selecting by vendor/board/shape needs no per-file edits:
    vendor:<dir>, board:<dir>, arch:<cpu> (from mcu:, fixture_arches),
    multi (machines:), slow (timeout >= 60)."""
    tags = set(meta.get("tags") or []) | fixture_arches(meta)
    rel = test_dir.relative_to(ROOT / "tests").parts
    if len(rel) >= 2:
        tags |= {f"vendor:{rel[0]}", f"board:{rel[1]}"}
    if meta.get("machines"):
        tags.add("multi")
    if (meta.get("timeout") or 0) >= 60:
        tags.add("slow")
    return tags


def pop_multi(argv: list, flag: str) -> set:
    """Collect every `flag value` pair (value may be comma-separated)."""
    out = set()
    while flag in argv:
        i = argv.index(flag)
        if i + 1 >= len(argv):
            die(f"{flag} needs a value")
        out |= {t for t in argv[i + 1].split(",") if t}
        del argv[i:i + 2]
    return out


def write_junit(path: Path, results: list) -> None:
    """One JUnit <testcase> per fixture, for CI result viewers. FAIL and XPASS
    are <failure>; xfail and skip statuses are <skipped>. The UART diff stays in
    the job log (see assert_output); the failure text names the status only."""
    from xml.sax.saxutils import quoteattr
    cases = []
    for r in results:
        head = f'<testcase classname="sim-fixtures" name={quoteattr(r["name"])} time="{r["duration_s"] or 0}"'
        if r["status"] in (FAIL, XPASS):
            body = f'><failure message={quoteattr(r["status"] + ": " + (r["reason"] or "see job log"))}/></testcase>'
        elif r["status"] != PASS:
            body = f'><skipped message={quoteattr(r["status"])}/></testcase>'
        else:
            body = "/>"
        cases.append("  " + head + body)
    fails = sum(r["status"] in (FAIL, XPASS) for r in results)
    skips = sum(r["status"] not in (PASS, FAIL, XPASS) for r in results)
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                    f'<testsuite name="sim-fixtures" tests="{len(results)}" failures="{fails}" skipped="{skips}">\n'
                    + "\n".join(cases) + "\n</testsuite>\n")


def main() -> int:
    argv = sys.argv[1:]
    # --ci: run only fixtures that opted into this engine's CI gate (`ci:
    # [<engine>, ...]` in test.yaml). Opt-IN by design: a new fixture never
    # silently grows CI -- adding one to the gate is a deliberate, reviewable
    # line in its test.yaml. Edge-case/stress suites (multi-boot OTA,
    # robustness pytest drills, 2-node radio runs) stay local/nightly.
    ci_only = "--ci" in argv
    if ci_only:
        argv.remove("--ci")
    # --gate: the per-push tier. One cheap boot fixture per MCU family, so an
    # engine change is checked against every family we model rather than against
    # whichever families happened to accumulate `ci:` lines. `--ci` stays the
    # wider set for manual and scheduled runs; the full suite (no flag) is the
    # pre-push gate and runs locally, where macOS minutes are free.
    gate_only = "--gate" in argv
    if gate_only:
        argv.remove("--gate")
        if ci_only:
            die("--gate and --ci select different tiers; pass one")
    # --skip-xfail: see run_test. Per-push gating runs only what can actually
    # regress; the expected-failure set is left in the manifests and picked up
    # by any run that omits this flag.
    skip_xfail = "--skip-xfail" in argv
    if skip_xfail:
        argv.remove("--skip-xfail")
    # --wireless / --no-wireless: only the radio and modem fixtures, or
    # everything else. They are a sixth of the suite and most of its wall time.
    wireless_only = "--wireless" in argv
    if wireless_only:
        argv.remove("--wireless")
    wired_only = "--no-wireless" in argv
    if wired_only:
        argv.remove("--no-wireless")
        if wireless_only:
            die("--wireless and --no-wireless select opposite sets; pass one")
    if "--published" in argv:
        argv.remove("--published")
    # --tag A[,B] (repeatable): keep fixtures carrying ANY listed tag.
    # --exclude-tag: drop fixtures carrying any listed tag. --list-tags prints
    # each selected fixture with its tags and exits without running anything.
    # Combines with --ci (AND). See fixture_tags for the implicit tags.
    want_tags = pop_multi(argv, "--tag")
    drop_tags = pop_multi(argv, "--exclude-tag")
    list_only = "--list-tags" in argv
    if list_only:
        argv.remove("--list-tags")
    engine = "pyrite"
    if "--engine" in argv:
        i = argv.index("--engine")
        if i + 1 >= len(argv):
            die("--engine needs a name")
        engine = argv[i + 1]
        del argv[i:i + 2]
    if engine not in ENGINES:
        die(f"unknown engine {engine!r}; known: {sorted(ENGINES)}")
    # --jobs N: fixtures run N at a time (default 4). Each sim is ~1.5 cores and
    # writes only work/<name>.* files, so fixtures do not share state.
    jobs = 4
    if "--jobs" in argv:
        i = argv.index("--jobs")
        if i + 1 >= len(argv) or not argv[i + 1].isdigit() or int(argv[i + 1]) < 1:
            die("--jobs needs a positive integer")
        jobs = int(argv[i + 1])
        del argv[i:i + 2]
    json_path = None
    work_dir = None
    if "--work" in argv:
        i = argv.index("--work")
        if i + 1 >= len(argv):
            die("--work needs a directory")
        work_dir = Path(argv[i + 1]).resolve()
        del argv[i:i + 2]
    if "--sim-args" in argv:
        i = argv.index("--sim-args")
        if i + 1 >= len(argv):
            die('--sim-args needs a quoted flag string, e.g. --sim-args "--profile-dir /tmp/p"')
        SIM_ARGS[:] = shlex.split(argv[i + 1])
        del argv[i:i + 2]
    if "--json" in argv:
        i = argv.index("--json")
        if i + 1 >= len(argv):
            die("--json needs a path")
        json_path = Path(argv[i + 1]).resolve()
        del argv[i:i + 2]

    junit_path = None
    if "--junit" in argv:
        i = argv.index("--junit")
        if i + 1 >= len(argv):
            die("--junit needs a path")
        junit_path = Path(argv[i + 1]).resolve()
        del argv[i:i + 2]

    if len(argv) < 1:
        die("usage: run-smoke.sh <sim-binary> [--engine <name>] [--ci] "
            "[--tag T] [--exclude-tag T] [--list-tags] [--skip-xfail] [--jobs N] [--json <path>] [--junit <path>] [--sim-args \"<flags>\"] [test-dir ...]")
    sim = Path(argv[0]).resolve()
    if not sim.exists():
        die(f"sim binary not found: {sim}")

    if len(argv) > 1:
        test_dirs = [Path(a).resolve() for a in argv[1:]]
        # A shell glob like tests/stm/nucleo-f072rb/*/ also matches support
        # directories that are not fixtures (`_hal/` holds a build script and a
        # linker script). Skip those visibly rather than die on the missing manifest.
        for d in [d for d in test_dirs if not (d / "test.yaml").exists()]:
            print(f"{DIM}- {d.name}  (no test.yaml, not a fixture){RST}")
        test_dirs = [d for d in test_dirs if (d / "test.yaml").exists()]
    else:
        test_dirs = sorted(p.parent for p in (ROOT / "tests").rglob("test.yaml"))
    if wireless_only or wired_only:
        total = len(test_dirs)
        test_dirs = [d for d in test_dirs if wireless_only == is_wireless(
            yaml.safe_load((d / "test.yaml").read_text()) or {}, d)]
        print(f"{DIM}{'--wireless' if wireless_only else '--no-wireless'}: "
              f"{len(test_dirs)} of {total} fixture(s){RST}")
    if ci_only:
        selected = []
        for d in test_dirs:
            meta = yaml.safe_load((d / "test.yaml").read_text()) or {}
            if engine in ci_engines(meta, d):
                selected.append(d)
        skipped = len(test_dirs) - len(selected)
        test_dirs = selected
        if not test_dirs:
            die(f"--ci selected no fixtures -- no test.yaml lists `{engine}` "
                f"under `ci:` in this checkout")
        print(f"{DIM}--ci ({engine}): {len(test_dirs)} gate fixture(s), "
              f"{skipped} not in this engine's gate{RST}")
    if gate_only:
        all_families = {family_of(d) for d in test_dirs}
        selected, exempt = [], {}
        for d in test_dirs:
            meta = yaml.safe_load((d / "test.yaml").read_text()) or {}
            if engine in gate_engines(meta, d):
                selected.append(d)
            reason = (meta.get("gate_exempt") or {}).get(engine) \
                if isinstance(meta.get("gate_exempt"), dict) else meta.get("gate_exempt")
            if reason:
                exempt.setdefault(family_of(d), str(reason))
        test_dirs = selected
        if not test_dirs:
            die(f"--gate selected no fixtures -- no test.yaml lists `{engine}` "
                f"under `gate:` in this checkout")
        covered = {family_of(d) for d in test_dirs}
        print(f"{DIM}--gate ({engine}): {len(test_dirs)} fixture(s) across "
              f"{len(covered)}/{len(all_families)} families{RST}")
        # A family with no gate fixture is invisible to every push, which is the
        # failure this tier exists to prevent -- say so rather than quietly
        # passing. Not fatal: families are added before their gate fixture is.
        # `gate_exempt: "<reason>"` in a family's test.yaml says the omission is
        # deliberate (not our code, engine does not target it, too slow for the
        # push tier). Exempt families are listed with their reason and do not
        # count as gaps, so the gap list stays a real to-do rather than noise
        # everyone learns to scroll past.
        missing = sorted(all_families - covered - set(exempt))
        if missing:
            print(f"{YELLOW}note: {len(missing)} family/families have no gate "
                  f"fixture: {', '.join(missing)}{RST}")
        shown = sorted(f for f in exempt if f not in covered)
        if shown:
            print(f"{DIM}gate-exempt families ({len(shown)}): "
                  + "; ".join(f"{f} -- {exempt[f]}" for f in shown) + RST)
        doubled = sorted(f for f in covered
                         if sum(family_of(d) == f for d in test_dirs) > 1)
        if doubled:
            print(f"{YELLOW}note: more than one gate fixture in: "
                  f"{', '.join(doubled)}{RST}")
    if want_tags or drop_tags or list_only:
        tagged = [(d, fixture_tags(yaml.safe_load((d / "test.yaml").read_text()) or {}, d))
                  for d in test_dirs]
        tagged = [(d, t) for d, t in tagged
                  if (not want_tags or t & want_tags) and not t & drop_tags]
        if list_only:
            for d, t in tagged:
                print(f"{d.relative_to(ROOT / 'tests')}  {' '.join(sorted(t))}")
            return 0
        test_dirs = [d for d, _ in tagged]
        if not test_dirs:
            die(f"tag filter selected no fixtures (--tag {sorted(want_tags)} "
                f"--exclude-tag {sorted(drop_tags)})")
        print(f"{DIM}tags: {len(test_dirs)} fixture(s) selected{RST}")
    if not test_dirs:
        die("no tests found (tests/**/test.yaml)")

    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        print(f"{DIM}keeping run artifacts in {work_dir}{RST}")
    with (contextlib.nullcontext(str(work_dir)) if work_dir is not None
          else tempfile.TemporaryDirectory()) as tmp:
        work = Path(tmp)

        print(f"{DIM}running {len(test_dirs)} smoke test(s) against {sim.name} "
              f"(engine: {engine}, {jobs} at a time){RST}\n")
        with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
            results = list(pool.map(
                lambda d: run_test(sim, d, work, engine, skip_xfail),
                test_dirs))

    counts = {s: sum(r["status"] == s for r in results)
              for s in (PASS, FAIL, XFAIL, XPASS, SKIP_HARDWARE, SKIP_XFAIL,
                        SKIP_NOELF)}
    total = len(results)
    ok = counts[FAIL] == 0 and counts[XPASS] == 0
    print()
    colour = GREEN if ok else RED
    tail = ", ".join(f"{n} {s.lower()}" for s, n in counts.items() if s != PASS and n)
    print(f"{colour}{counts[PASS]}/{total} passed{RST}" + (f"  {DIM}({tail}){RST}" if tail else ""))
    if counts[SKIP_HARDWARE] and ci_only:
        # A gate made of hardware-only manifests would print N/N and prove
        # nothing; say so where it can be seen.
        print(f"{YELLOW}note: {counts[SKIP_HARDWARE]} hardware-only fixture(s) in the "
              f"gate ran nothing{RST}")

    if json_path:
        json_path.write_text(json.dumps({
            "engine": engine,
            "sim": str(sim),
            "sim_fixtures_sha": git_head(ROOT),
            "models": "cached" if CACHED_MCUS else "published",
            "cache_fetched": {m: cache_fetched(m) for m in sorted(CACHED_MCUS)} or None,
            "counts": counts,
            "ok": ok,
            "results": results,
        }, indent=2) + "\n")
        print(f"{DIM}wrote {json_path}{RST}")
    if junit_path:
        write_junit(junit_path, results)
        print(f"{DIM}wrote {junit_path}{RST}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
