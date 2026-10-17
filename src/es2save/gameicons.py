# Copyright (C) 2026 KiasuBurger
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""The game's containers and the textures in them, which is where its icons are.

The icons are Texture2D assets in the game's .utoc/.ucas containers, which are not encrypted
but are compressed (see kraken.py). A .utoc is the index of its .ucas: the files in it by
path, where each is, and the compressed blocks the .ucas is made of. The icons are looked up
in it by path, and the pixels of each are found in its asset by the pixel format's name,
which the size is in front of, rather than by reading the asset properly.

    read_assets(game, prefix)           yields (name, bytes) for the assets below a path
    find_texture, decode_texture        find a texture in an asset's bytes and decode its pixels
    write_png(path, pixels)             writes them out

The editor's icons.py says which icons it wants and keeps them as PNGs.
"""

import glob
import os
import re
import struct
import zlib
from collections.abc import Callable, Iterable, Iterator
from typing import BinaryIO

import numpy as np

from . import kraken
from .gamepaths import PAKS

Block = tuple[int, int, int]  # of a container: (offset, packed size or 0 if stored as it is, size)

FORMATS = {'PF_DXT1': 'DXT1', 'PF_DXT5': 'DXT5', 'PF_B8G8R8A8': 'BGRA8'}
BLOCK_BYTES = {'DXT1': 8, 'DXT5': 16}


# -- finding the game --------------------------------------------------------------------


# -- the containers ----------------------------------------------------------------------

_NONE = 0xFFFFFFFF


def _string(d: bytes, p: int) -> tuple[str, int]:
    (n,) = struct.unpack_from('<i', d, p)
    p += 4
    if n < 0:
        return d[p : p - 2 * n].decode('utf-16-le').rstrip('\0'), p - 2 * n
    return d[p : p + n].rstrip(b'\0').decode('latin-1'), p + n


def read_toc(
    path: str, prefix: str, wanted: Callable[[str], object] | None = None
) -> list[tuple[str, list[Block], int, int]]:
    """The assets below prefix in the container whose .utoc is at path, those that wanted
    accepts by name if it is given, as [(name, blocks, start, length)]: the path below
    prefix without its .uasset, the blocks of the .ucas the asset is in, for read_blocks,
    and where it is in them."""
    with open(path, 'rb') as f:
        d = f.read()
    try:
        if d[:16] != b'-==--==--==--==-':
            raise ValueError('%s is not the index of a container' % path)
        (header, entries, blocks, _, methods, method_length, block_size, directory_size) = struct.unpack_from(
            '<8I', d, 20
        )
        (seeds,) = struct.unpack_from('<I', d, 84)
        (unhashed,) = struct.unpack_from('<I', d, 96)
        chunks_at = header + 12 * entries
        blocks_at = chunks_at + 10 * entries + 4 * seeds + 4 * unhashed
        directory_at = blocks_at + 12 * blocks + methods * method_length
        if not directory_size:
            return []
        if d[80] & 6:
            raise ValueError('%s is encrypted or signed' % path)
        d, toc = d[directory_at : directory_at + directory_size], d
        mount, p = _string(d, 0)
        if not (mount.startswith(prefix) or prefix.startswith(mount)):
            return []  # most of them: nothing below where this one's files are is wanted
        (n,) = struct.unpack_from('<I', d, p)
        directories = [struct.unpack_from('<4I', d, p + 4 + 16 * i) for i in range(n)]
        p += 4 + 16 * n
        (n,) = struct.unpack_from('<I', d, p)
        files = [struct.unpack_from('<3I', d, p + 4 + 12 * i) for i in range(n)]
        p += 4 + 12 * n
        (n,) = struct.unpack_from('<I', d, p)
        p += 4
        strings = []
        for _ in range(n):
            text, p = _string(d, p)
            strings.append(text)
        found = []
        todo = [(0, mount)]
        while todo:
            i, parent = todo.pop()
            name, child, sibling, file = directories[i]
            here = parent + (strings[name] + '/' if name != _NONE else '')
            while file != _NONE:
                name, file, chunk = files[file]
                name = here + strings[name]
                if name.startswith(prefix) and name.endswith('.uasset'):
                    name = name[len(prefix) : -len('.uasset')]
                    if not wanted or wanted(name):
                        found.append((name, chunk))
            if child != _NONE and (here.startswith(prefix) or prefix.startswith(here)):
                todo.append((child, here))
            if sibling != _NONE:
                todo.append((sibling, parent))
        names = [
            toc[q : q + method_length].rstrip(b'\0')
            for q in range(directory_at - methods * method_length, directory_at, method_length)
        ]
        out = []
        for name, chunk in sorted(found):
            q = chunks_at + 10 * chunk
            offset, length = int.from_bytes(toc[q : q + 5], 'big'), int.from_bytes(toc[q + 5 : q + 10], 'big')
            run = []
            for q in range(
                blocks_at + 12 * (offset // block_size), blocks_at + 12 * ((offset + length - 1) // block_size + 1), 12
            ):
                method = toc[q + 11]
                if method and names[method - 1] != b'Oodle':
                    raise ValueError('%s is compressed with %s' % (path, names[method - 1].decode()))
                packed, size = (
                    int.from_bytes(toc[q + 5 : q + 8], 'little'),
                    int.from_bytes(toc[q + 8 : q + 11], 'little'),
                )
                run.append((int.from_bytes(toc[q : q + 5], 'little'), packed if method else 0, size))
            out.append((name, run, offset % block_size, length))
        return out
    except struct.error, IndexError, UnicodeDecodeError:
        raise ValueError('%s is damaged' % path) from None


def read_assets(
    game: str,
    prefix: str,
    wanted: Callable[[str], object] | None = None,
    progress: Callable[[int, int], object] | None = None,
) -> Iterator[tuple[str, bytes]]:
    """Yields (name, bytes) of each asset below prefix in the game's containers, as read_toc
    finds them. progress is called with (done, total) after each."""
    paks = os.path.join(game, PAKS)
    found = []
    for toc in sorted(glob.glob(os.path.join(glob.escape(paks), '*.utoc'))):
        found += [(toc[: -len('.utoc')] + '.ucas', *asset) for asset in read_toc(toc, prefix, wanted)]
    files = {}
    try:
        for i, (container, name, blocks, start, length) in enumerate(found):
            if container not in files:
                files[container] = open(container, 'rb')  # noqa: SIM115 (closed below)
            yield name, bytes(read_blocks(files[container], blocks)[start : start + length])
            if progress:
                progress(i + 1, len(found))
    finally:
        for f in files.values():
            f.close()


def find_texture(d: bytes) -> tuple[str, int, int, int] | None:
    """Returns (format, width, height, offset of the pixels) of the texture in an asset, or None."""
    m = re.search(rb'PF_[A-Za-z0-9_]+\x00', d)
    if not m or m.group()[:-1].decode() not in FORMATS or m.start() < 16:
        return None
    fmt = FORMATS[m.group()[:-1].decode()]
    # FTexturePlatformData: SizeX, SizeY, packed data, then the pixel format name
    width, height = struct.unpack_from('<ii', d, m.start() - 16)
    if not (0 < width <= 8192 and 0 < height <= 8192):
        return None
    need = texture_bytes(fmt, width, height)
    # every mip ends with its SizeX, SizeY, SizeZ; mip 0's pixels sit just before that marker
    marker = struct.pack('<iii', width, height, 1)
    p = d.find(marker, m.end())
    while p >= 0:
        if p - need >= m.end():
            return fmt, width, height, p - need
        p = d.find(marker, p + 1)
    return None


# -- textures ----------------------------------------------------------------------------


def texture_bytes(fmt: str, width: int, height: int) -> int:
    """The size of a texture's pixel data."""
    if fmt == 'BGRA8':
        return width * height * 4
    return ((width + 3) // 4) * ((height + 3) // 4) * BLOCK_BYTES[fmt]


def _rgb565(c: np.ndarray) -> np.ndarray:
    r = ((c >> 11) & 31).astype(np.uint16)
    g = ((c >> 5) & 63).astype(np.uint16)
    b = (c & 31).astype(np.uint16)
    return np.stack([(r * 527 + 23) >> 6, (g * 259 + 33) >> 6, (b * 527 + 23) >> 6], axis=-1)


def _colour_blocks(blocks: np.ndarray, four_colour_always: bool) -> np.ndarray:
    """Decodes the 8-byte colour part of BC1/BC3 blocks to (N, 4, 4, 4) RGBA (alpha from BC1)."""
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    n = len(blocks)
    palette = np.zeros((n, 4, 4), dtype=np.uint16)
    palette[:, :, 3] = 255
    palette[:, 0, :3] = p0
    palette[:, 1, :3] = p1
    four = np.ones(n, dtype=bool) if four_colour_always else (c0 > c1)
    palette[:, 2, :3] = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    palette[:, 3, :3] = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    palette[~four, 3, 3] = 0
    bits = blocks[:, 4:8].astype(np.uint32)
    code = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = (code[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    return palette[np.arange(n)[:, None], idx].reshape(n, 4, 4, 4)


def _alpha_blocks(blocks: np.ndarray) -> np.ndarray:
    """Decodes the 8-byte interpolated alpha part of BC3 blocks to (N, 4, 4)."""
    a0 = blocks[:, 0].astype(np.uint16)
    a1 = blocks[:, 1].astype(np.uint16)
    n = len(blocks)
    palette = np.zeros((n, 8), dtype=np.uint16)
    palette[:, 0], palette[:, 1] = a0, a1
    eight = a0 > a1
    for i in range(1, 7):
        palette[:, i + 1] = np.where(
            eight, ((7 - i) * a0 + i * a1) // 7, ((5 - i) * a0 + i * a1) // 5 if i < 5 else (0 if i == 5 else 255)
        )
    code = np.zeros(n, dtype=np.uint64)
    for i in range(6):
        code |= blocks[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    idx = (code[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))) & np.uint64(7)
    return palette[np.arange(n)[:, None], idx.astype(np.int64)].reshape(n, 4, 4)


def decode_texture(fmt: str, width: int, height: int, data: bytes) -> np.ndarray:
    """Decodes a texture's pixel data to an (H, W, 4) uint8 RGBA array."""
    if fmt == 'BGRA8':
        return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)[:, :, [2, 1, 0, 3]]
    bw, bh = (width + 3) // 4, (height + 3) // 4
    blocks = np.frombuffer(data, dtype=np.uint8).reshape(bw * bh, BLOCK_BYTES[fmt])
    if fmt == 'DXT1':
        px = _colour_blocks(blocks, False)
    else:
        px = _colour_blocks(blocks[:, 8:], True)
        px[:, :, :, 3] = _alpha_blocks(blocks[:, :8])
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:height, :width].astype(np.uint8)


def write_png(path: str, rgba: np.ndarray) -> None:
    """Writes an (H, W, 4) uint8 array as an RGBA PNG."""
    h, w, _ = rgba.shape
    raw = np.zeros((h, w * 4 + 1), dtype=np.uint8)  # filter byte 0 per row
    raw[:, 1:] = rgba.reshape(h, w * 4)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b'IDAT', zlib.compress(raw.tobytes(), 3)))
        f.write(chunk(b'IEND', b''))


# -- extraction --------------------------------------------------------------------------


def read_blocks(ucas: BinaryIO, blocks: Iterable[Block]) -> bytearray:
    """The bytes of a run of a container's blocks, each (offset, packed size or 0 if it is
    stored as it is, size)."""
    out = bytearray()
    for offset, packed, size in blocks:
        ucas.seek(offset)
        data = ucas.read(packed or size)
        out += kraken.decompress(data, size) if packed else data
    return out
