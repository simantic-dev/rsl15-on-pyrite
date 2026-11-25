# onsemi RSL15 SDK samples

The RSL15 SDK samples (CMSIS-Pack `ONSemiconductor.RSL15`, tested with 1.12.374), each run
unmodified on pyrite's `RSL15` model. Each `<sample>/test.yaml` is one SDK sample.

The SDK is licensed per user (onsemi Software Use Agreement), so neither its sources nor ELFs
built from it are committed: every fixture is `elf_external: true` and reports SKIP-NOELF until
you build it from your own pack:

    ./build_sdk.py <unzipped ONSemiconductor.RSL15.<ver>.pack dir> .

`build_sdk.py` builds every gcc sample headless the way the onsemi IDE does (the sample's
`.rteconfig` components resolved through the pack's `.pdsc`, the Debug configuration of its
`.cproject`) and drops `<sample>.elf` in each fixture directory. The open-source Arm parts some
samples use (CMSIS 5.9.0, CMSIS-FreeRTOS 10.4.6) are found through `ONSEMI_DEPS`.

What the fixtures check is each sample's own success signal: its PASS line (CryptoCell samples),
its UART/RTT text, its LEDs as pin waveforms (`pad_edges`, numbers derived from the registers the
sample writes), or the trace of the function its success path runs. Per-instruction CPU cycles are
the Cortex-M4 table, unmeasured on the M33; nothing here has been compared against RSL15 silicon.
