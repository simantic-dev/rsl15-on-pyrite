# onsemi RSL15 SDK on pyrite

onsemi's RSL15 SDK samples, **unmodified**, running on the pyrite simulator's RSL15 model: boot
chain, CryptoCell-312, TrustZone, low power (Standby and Sleep with ROM resume), timers, DMA, MPU,
I2C, SPI, UART, PWM, LIN. Each sample is checked by its own success signal.

This repository contains **no onsemi code or binaries**: only the build script, the test
descriptions (`test.yaml`, board overlays), the check scripts and a demo. You build the samples
from your own copy of the SDK.

## SDK version

| | |
|---|---|
| Package | `ONSemiconductor.RSL15` CMSIS-Pack **1.12.374** (November 2025) |
| Where | [onsemi RSL15 product page](https://www.onsemi.com/products/wireless-connectivity/bluetooth-low-energy/rsl15), *Software* → RSL15 CMSIS-Pack |
| Toolchain | Arm GNU Toolchain (`arm-none-eabi-gcc` on `PATH`; tested with 15.3) |
| Extra (FreeRTOS samples only) | CMSIS 5.9.0 and CMSIS-FreeRTOS 10.4.6 packs, folder in `ONSEMI_DEPS` |

The SDK is covered by onsemi's Software Use Agreement; get it from onsemi and accept that
agreement yourself. Other pack versions may build but are not what these checks were made with.

## Run it

```sh
pip install pyyaml
unzip ONSemiconductor.RSL15.1.12.374.pack -d rsl15-pack
cd tests/onsemi/rsl15 && ./build_sdk.py ../../../rsl15-pack . && cd -   # builds every sample, headless
python3 scripts/run_smoke.py "$(command -v sim)" --engine pyrite tests/onsemi/rsl15/*
SIM="$(command -v sim)" tests/onsemi/rsl15/demo/demo                    # six-scene demo, ~5 s
```

`sim` is the pyrite simulator (ask Simantic for access).

## What runs

Of the pack's 54 gcc samples, 39 run and are checked (40 fixtures). Not yet: the Bluetooth samples (the
RivieraWaves baseband model is in progress), `sai_cmsis` (two boards over PCM), and two samples
that do not build from the 1.12 pack as shipped.

Each `tests/onsemi/rsl15/<sample>/test.yaml` says in its header what is checked and why it proves
the sample worked. Board wiring the EVB provides with jumpers (UART TX->RX, SPI0<->SPI1, the LIN
transceiver echo) is stated in that sample's `overlay.board`.
