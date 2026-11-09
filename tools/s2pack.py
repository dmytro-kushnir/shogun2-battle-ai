"""Writes Shogun 2 Complete Edition packs ("PFHs").

Layout, as read from the game's own packs (see tools/packtool.ps1):
    header  "PFHs", u32 type, u32 0, u32 0, u32 file_count, u32 index_size, u64 timestamp   (32 bytes)
    index   per file: u64 offset, u32 size, u32 stored_size, [u64 timestamp if type & 0x40], name\\0
    data    each file as a raw LZ4 block (the game's packs compress every entry, even when it grows)
Type 0x24 = movie pack (low nibble 4) + the 0x20 flag the CE packs carry. The engine loads every movie pack
in data\\ by itself, without a mod list - Remastered CE ships its add-on pack the same way.

    python tools/s2pack.py OUT.pack  path/in/pack=local_file  [...]
"""
import struct
import sys
from pathlib import Path

TYPE_MOVIE = 0x24


def lz4_literals(data: bytes) -> bytes:
    """A valid LZ4 block holding `data` as one run of literals (no matches): simple and always accepted."""
    n = len(data)
    if n < 15:
        return bytes([n << 4]) + data
    out = bytearray([0xF0])
    rest = n - 15
    while rest >= 255:
        out.append(255)
        rest -= 255
    out.append(rest)
    return bytes(out) + data


def build(out: Path, files: dict[str, bytes], pack_type: int = TYPE_MOVIE) -> None:
    names = sorted(files, key=str.lower)  # the game's packs keep their index sorted
    blobs = [lz4_literals(files[n]) for n in names]
    index_size = sum(8 + 4 + 4 + len(n.encode("ascii")) + 1 for n in names)
    offset = 32 + index_size
    index = bytearray()
    for name, blob in zip(names, blobs):
        index += struct.pack("<QII", offset, len(files[name]), len(blob)) + name.encode("ascii") + b"\0"
        offset += len(blob)
    header = b"PFHs" + struct.pack("<IIIIIQ", pack_type, 0, 0, len(names), index_size, 0)
    out.write_bytes(header + bytes(index) + b"".join(blobs))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    entries = {}
    for arg in sys.argv[2:]:
        name, _, local = arg.partition("=")
        entries[name.replace("/", "\\")] = Path(local).read_bytes()
    build(Path(sys.argv[1]), entries)
    print(f"wrote {sys.argv[1]}: {len(entries)} files")
