#!/usr/bin/env python3
"""Build onsemi RSL15 SDK gcc samples headless from the user's own CMSIS-Pack.

The pack is licensed per user, so nothing from it is vendored here: pass the
unzipped ONSemiconductor.RSL15.<ver>.pack directory.

  build_sdk.py <pack_dir> <out_dir> [sample ...]

Each sample's <name>.rteconfig lists the pack files it uses; files marked
attr="config" are taken from the sample's RTE/Device/RSL15 copy when present.
"""
import os, re, shlex, subprocess, sys, xml.etree.ElementTree as ET

CC = "arm-none-eabi-gcc"
KEY = ("Cclass", "Cgroup", "Csub", "Cvariant")
CPU = ["-mcpu=cortex-m33", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv5-sp-d16"]
CFLAGS = CPU + ["-Os", "-g3", "-ffunction-sections", "-fdata-sections",
                "-DRSL15_CID=202", "-D_RTE_", "-std=gnu11",
                # GCC 14 turned these C diagnostics into errors; the SDK was built with an older
                # GCC that warned, as the onsemi IDE toolchain still does.
                "-Wno-error=implicit-function-declaration", "-Wno-error=int-conversion",
                "-Wno-error=incompatible-pointer-types"]
LDFLAGS = CPU + ["--specs=nano.specs", "--specs=nosys.specs", "-nostartfiles",
                 "-Wl,--gc-sections"]


def holds(cid, conds, selected):
    """Evaluate a pdsc <condition> for target RSL15 + GCC: all <require>, any
    <accept> (if present), and no <deny> hold. Components match on the KEY attrs."""
    if cid not in conds:
        return False
    def ok(e):
        if "condition" in e.attrib:
            return holds(e.get("condition"), conds, selected)
        if "Tcompiler" in e.attrib:
            return e.get("Tcompiler") == "GCC"
        if "Dname" in e.attrib:
            return e.get("Dname") == "RSL15"
        want = {k: e.get(k) for k in KEY if e.get(k)}
        return any(all(s.get(k) == v for k, v in want.items()) for s in selected)
    kids = list(conds[cid])
    req = [ok(e) for e in kids if e.tag == "require"]
    acc = [ok(e) for e in kids if e.tag == "accept"]
    den = [ok(e) for e in kids if e.tag == "deny"]
    return all(req) and (not acc or any(acc)) and not any(den)


def dep_dirs(pack):
    """Open-source deps (CMSIS-FreeRTOS, CMSIS_5) in $ONSEMI_DEPS, default onsemi/deps."""
    r = os.path.abspath(os.environ.get("ONSEMI_DEPS", os.path.join(pack, "..", "..", "..", "deps")))
    return [os.path.join(r, d) for d in sorted(os.listdir(r))] if os.path.isdir(r) else []


def resolve(pack, name):
    """A pdsc path inside the pack, or else the same path under a dep."""
    p = os.path.join(pack, name)
    if os.path.exists(p):
        return p
    for d in dep_dirs(pack):
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return p


def files(sample_dir, pack, excl=(), prefer_local=False):
    cfg = [f for f in os.listdir(sample_dir) if f.endswith(".rteconfig")]
    rte = os.path.join(sample_dir, "RTE", "Device", "RSL15")
    srcs, incs, libs, ld = [], {os.path.join(sample_dir, "include"), rte,
                                os.path.join(pack, "firmware", "include")}, [], None
    if cfg:
        # The pdsc's own file list for each selected component: the rteconfig
        # copy is only what the IDE last wrote and can miss files.
        # Components and conditions from the pack's pdsc, then the deps' (CMSIS-FreeRTOS).
        pdscs = [os.path.join(pack, x) for x in os.listdir(pack) if x.endswith(".pdsc")][:1]
        pdscs += [os.path.join(d, x) for d in dep_dirs(pack)
                  for x in os.listdir(d) if x.endswith(".pdsc")]
        comps, conds = {}, {}
        for pf in pdscs:
            for c in ET.parse(pf).iter("component"):
                if c.find("files") is None:  # <examples> stubs repeat the key with no files
                    continue
                comps.setdefault(tuple(c.get(k) for k in KEY), c)
            for c in ET.parse(pf).iter("condition"):
                conds.setdefault(c.get("id"), c)
        picked = [comps.get(tuple(c.get(k) for k in KEY))
                  for c in ET.parse(os.path.join(sample_dir, cfg[0])).iter("component")]
        listed = [f for c in picked if c is not None for f in c.iter("file")]
        sel = list(ET.parse(os.path.join(sample_dir, cfg[0])).iter("component"))
        for f in listed + list(ET.parse(os.path.join(sample_dir, cfg[0])).iter("file")):
            cond = f.get("condition", "")
            if cond and not holds(cond, conds, sel):
                continue
            name, cat = f.get("name"), f.get("category")
            path = resolve(pack, name)
            local = os.path.join(rte, os.path.basename(name))
            # The project's own copy in RTE/<group>/ is what the IDE compiles: config files always,
            # every file for an older project that carries its whole RTE (the loadable blinky).
            if os.path.exists(local) and (f.get("attr") == "config" or prefer_local):
                path = local
            if cat in ("source", "sourceC", "sourceAsm"):
                if path not in srcs:
                    srcs.append(path)
            elif cat == "header":
                incs.add(os.path.dirname(path))
            elif cat == "include":
                incs.add(path.rstrip("/"))
            elif cat == "library":
                libs.append(path)
            elif cat == "linkerScript":
                ld = path
    # IDE-generated config headers (e.g. RTE/RTOS/FreeRTOSConfig.h) live in RTE/<group>/.
    rte_root = os.path.join(sample_dir, "RTE")
    for root, _, fs in (os.walk(rte_root) if os.path.isdir(rte_root) else []):
        if any(f.endswith(".h") for f in fs):
            incs.add(root)
    for root, _, fs in os.walk(sample_dir):
        rel = os.path.relpath(root, sample_dir)
        if os.sep + "RTE" in root[len(sample_dir):] or \
           any(rel == e or rel.startswith(e + os.sep) for e in excl):
            continue
        for f in fs:
            p = os.path.join(root, f)
            if f.endswith((".c", ".S", ".s")):
                srcs.append(p)
            elif f.endswith(".h"):
                incs.add(root)
    return srcs, sorted(incs), libs, ld


def cproject(sample_dir, pack, config="Debug"):
    """Debug-config defines, include paths, flags and link inputs from the IDE's
    .cproject: the ground truth for what the GCC build needs beyond the pdsc."""
    out = {"defs": [], "incs": [], "cflags": [], "ldflags": [], "objs": [], "ld": None,
           "excl": []}
    p = os.path.join(sample_dir, ".cproject")
    if not os.path.exists(p):
        return out
    cfgs = list(ET.parse(p).getroot().iter("configuration"))
    cfg = next((c for c in cfgs if c.get("name") == config), cfgs[0])

    def sub(v):
        v = v.strip().strip('"')
        v = re.sub(r"\$\{workspace_loc:/\$\{ProjName\}/?([^}]*)\}",
                   lambda m: os.path.join(sample_dir, m.group(1)).rstrip("/"), v)
        return re.sub(r"\$\{cmsis_pack_root\}/ONSemiconductor/RSL15/[^/]+", pack, v)

    # Each sourcePath entry excludes relative to its own folder; a folder that is an entry of its
    # own (secure_bootloader's `code`) is built even though the root entry excludes it.
    entries = [e for e in cfg.iter("entry") if e.get("kind") == "sourcePath"]
    roots = {(e.get("name") or "").strip("/") for e in entries}
    for e in entries:
        base = (e.get("name") or "").strip("/")
        for x in (e.get("excluding") or "").split("|"):
            x = os.path.join(base, x.strip("/")) if base else x.strip("/")
            if x and x not in roots:
                out["excl"].append(x)
    for o in cfg.iter("option"):
        sc, kind = o.get("superClass", ""), o.get("valueType")
        vals = [sub(l.get("value")) for l in o.findall("listOptionValue")]
        if kind == "definedSymbols" and "c.compiler.defs" in sc:
            out["defs"] += vals
        elif kind == "includePath" and "c.compiler.include.paths" in sc:
            out["incs"] += vals
        elif kind == "stringList" and "c.linker.scriptfile" in sc and vals:
            out["ld"] = vals[0]
        elif kind == "userObjs" and "c.linker.otherobjs" in sc:
            out["objs"] += vals
        elif kind == "enumerated" and sc.endswith("optimization.level"):
            # The project's own -O (Debug is -O0 in most samples); it follows CFLAGS' -Os, so wins.
            lvl = {"none": "-O0", "optimize": "-O1", "more": "-O2", "most": "-O3", "size": "-Os",
                   "debug": "-Og"}.get(o.get("value", "").rsplit(".", 1)[-1])
            if lvl:
                out["cflags"].append(lvl)
        elif kind == "string" and o.get("value") and "c.compiler.other" in sc:
            out["cflags"] += shlex.split(o.get("value"))
        elif kind == "string" and o.get("value") and "c.linker.other" in sc:
            out["ldflags"] += [sub(a) for a in shlex.split(o.get("value"))]
    return out





def rte_components(sample_dir, pack, gen):
    """Write the RTE_Components.h the IDE would generate: each selected
    component's <RTE_Components_h> text from the pdsc, plus the device header."""
    pdsc = [f for f in os.listdir(pack) if f.endswith(".pdsc")][0]
    text = {tuple(c.get(k) for k in KEY): (c.findtext("RTE_Components_h") or "")
            for c in ET.parse(os.path.join(pack, pdsc)).iter("component")
            if c.find("files") is not None}
    cfg = [f for f in os.listdir(sample_dir) if f.endswith(".rteconfig")]
    body = ['#define CMSIS_device_header "rsl15.h"']
    if cfg:
        for c in ET.parse(os.path.join(sample_dir, cfg[0])).iter("component"):
            body.append(text.get(tuple(c.get(k) for k in KEY), ""))
    os.makedirs(gen, exist_ok=True)
    with open(os.path.join(gen, "RTE_Components.h"), "w") as f:
        f.write("#ifndef RTE_COMPONENTS_H\n#define RTE_COMPONENTS_H\n"
                + "\n".join(body) + "\n#endif\n")


# A sample's own config switches, set as its README offers: (file, from, to).
# hardfault_handler prints over semihosting by default, which needs a debugger attached; its
# SWMTRACE_ENABLE switch prints the same report over swmTrace RTT instead.
CONFIG = {"hardfault_handler": ("include/app.h", "#define SWMTRACE_ENABLE                 (0)",
                                "#define SWMTRACE_ENABLE                 (1)")}


def build(sample_dir, pack, out, config="Debug", name=None, prefer_local=False):
    name = name or os.path.basename(sample_dir.rstrip("/"))
    if name in CONFIG:
        import shutil
        rel, old, new = CONFIG[name]
        staged = os.path.abspath(os.path.join(out, "gen", name + "-src", name))
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(sample_dir, staged)
        with open(os.path.join(staged, rel)) as f:
            text = f.read()
        assert old in text, (name, old)
        with open(os.path.join(staged, rel), "w") as f:
            f.write(text.replace(old, new))
        sample_dir = staged
    cp = cproject(sample_dir, pack, config)
    srcs, incs, libs, ld = files(sample_dir, pack, cp["excl"], prefer_local)
    # The IDE's link inputs pick one library variant (e.g. Release_Secure); the pdsc lists all.
    libs = cp["objs"] or libs
    cp["objs"] = []
    if cp["ld"]:
        # The IDE copies the pack's configuration/GCC script into RTE/ at build time.
        alt = os.path.join(pack, "firmware", "configuration", "GCC", os.path.basename(cp["ld"]))
        ld = cp["ld"] if os.path.exists(cp["ld"]) else (alt if os.path.exists(alt) else ld)
    gen = os.path.join(out, "gen", name)
    rte_components(sample_dir, pack, gen)
    # Sample-local dirs first, as the IDE does: they hold the project's own config headers.
    allinc = list(dict.fromkeys(incs + cp["incs"]))
    incs = [gen] + [i for i in allinc if i.startswith(sample_dir)] + \
           [i for i in allinc if not i.startswith(sample_dir)]
    if not ld:
        return name, "no linker script"
    # Into the sample's fixture directory when out holds one (build_sdk.py <pack> .).
    d = os.path.join(out, name)
    elf = os.path.join(d if os.path.isdir(d) else out, name + ".elf")
    elf = os.path.abspath(elf)
    defs = ["-D" + d for d in dict.fromkeys(cp["defs"])]
    # libfota.a exports the prebuilt FOTA stack's entry points as absolute Thumb addresses, which
    # binutils >= 2.40 will not reach with a BL ("Unknown destination type"); call through a register.
    if any(l.endswith("libfota.a") for l in libs):
        defs.append("-mlong-calls")
    cmd = [CC] + CFLAGS + defs + cp["cflags"] + ["-I" + i for i in incs] + srcs + LDFLAGS + \
          ["-T", ld, "-L" + os.path.dirname(ld), "-o", elf] + \
          ["-Wl,--start-group"] + libs + ["-Wl,--end-group"] + cp["ldflags"] + ["-lm"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=out)
    if r.returncode:
        err = [l for l in r.stderr.splitlines() if "error" in l]
        return name, "FAIL " + (err[0] if err else r.stderr[-300:])
    return name, "ok"


def main():
    pack, out, *only = sys.argv[1:]
    os.makedirs(out, exist_ok=True)
    root = os.path.join(pack, "firmware", "source", "samples", "gcc")
    names = only or sorted(os.listdir(root))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda n: build(os.path.join(root, n), pack, out),
                          [n for n in names if os.path.isdir(os.path.join(root, n))]))
    for n, s in res:
        print(f"{s[:4]:4} {n}  {s if s != 'ok' else ''}")
    print(f"{sum(s == 'ok' for _, s in res)}/{len(res)} built")
    # The bootloader fixtures boot the pack's own bootloader-compatible image.
    import glob, shutil
    for d in glob.glob(os.path.join(out, "bootloader_*")):
        for f in ("bootloader.elf",):
            if os.path.exists(os.path.join(out, "bootloader", f)):
                shutil.copy(os.path.join(out, "bootloader", f), d)
        shutil.copy(os.path.join(root, "bootloader", "utility", "blinky.bin"), d)
    # secure_bootloader boots the pack's loadable blinky (secure_bootloader/utilities/
    # loadable_samples/blinky.zip), built in its bl_D_Basic configuration: linked at the app
    # execution area with the size words the Basic bootloader checks.
    sb = os.path.join(out, "secure_bootloader_app")
    if os.path.isdir(sb) and os.path.exists(os.path.join(out, "secure_bootloader", "secure_bootloader.elf")):
        shutil.copy(os.path.join(out, "secure_bootloader", "secure_bootloader.elf"), sb)
        import zipfile
        src = os.path.abspath(os.path.join(out, "gen", "loadable_blinky"))
        shutil.rmtree(src, ignore_errors=True)
        zipfile.ZipFile(os.path.join(root, "secure_bootloader", "utilities", "loadable_samples",
                                     "blinky.zip")).extractall(src)
        status = build(src, pack, out, "bl_D_Basic", "blinky_bl", prefer_local=True)[1]
        if status == "ok":
            elf = os.path.join(out, "blinky_bl.elf")
            objcopy = os.path.join(os.path.dirname(CC), "arm-none-eabi-objcopy")
            subprocess.run([objcopy, "-O", "binary", elf, os.path.join(sb, "blinky_bl.bin")], check=True)
            os.remove(elf)
        print(f"{status[:4]:4} blinky_bl (secure_bootloader's loadable image, bl_D_Basic) {status if status != 'ok' else ''}")


if __name__ == "__main__":
    main()
