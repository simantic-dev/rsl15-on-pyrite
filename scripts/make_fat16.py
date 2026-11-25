#!/usr/bin/env python3
"""Write an empty FAT16 volume into a file, stdlib only (Linux and macOS).

    make_fat16.py <out.img> <size-bytes>

The file is sparse: only the boot sector, the two FATs and the root
directory are written, and the rest reads back as zeros. Layout follows the
Microsoft FAT spec for a 512-byte-sector volume with 2 KiB clusters.
"""
import struct
import sys

SECTOR = 512
SPC = 4          # sectors per cluster (2 KiB)
RSVD = 1         # reserved sectors (boot sector only)
NFATS = 2
ROOT_ENTRIES = 512


def write_fat16(path, size):
    total = size // SECTOR
    root_secs = ROOT_ENTRIES * 32 // SECTOR
    # Smallest FAT size (sectors) that covers every data cluster.
    fat_secs = 1
    while True:
        clusters = (total - RSVD - NFATS * fat_secs - root_secs) // SPC
        if (fat_secs * SECTOR) // 2 >= clusters + 2:
            break
        fat_secs += 1
    assert 4085 < clusters < 65525, "not a FAT16 cluster count"

    boot = bytearray(SECTOR)
    boot[0:3] = b"\xeb\x3c\x90"
    boot[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", boot, 11,
                     SECTOR, SPC, RSVD, NFATS, ROOT_ENTRIES,
                     total if total < 65536 else 0, 0xF8, fat_secs,
                     63, 255, 0, total if total >= 65536 else 0)
    struct.pack_into("<BBBI11s8s", boot, 36, 0x80, 0, 0x29, 0x5EC0FFEE,
                     b"ENDUROSAT  ", b"FAT16   ")
    boot[510:512] = b"\x55\xaa"

    fat = bytearray(fat_secs * SECTOR)
    fat[0:4] = b"\xf8\xff\xff\xff"  # media descriptor + end-of-chain for entry 1

    with open(path, "wb") as f:
        f.truncate(size)
        f.seek(0)
        f.write(boot)
        for i in range(NFATS):
            f.seek((RSVD + i * fat_secs) * SECTOR)
            f.write(fat)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    write_fat16(sys.argv[1], int(sys.argv[2]))
