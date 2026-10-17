# Copyright (C) 2026 KiasuBurger
#
# Based on kraken.cpp of ooz, the Kraken decompressor by Powzix
# (https://github.com/powzix/ooz), which is Copyright (C) 2016, Powzix
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

"""Decoder for Oodle Kraken, the compression of the game's .ucas containers.

Only decoding, and only Kraken (compressor 6), which is all the game uses. A compressed
buffer is a 2-byte header and then quanta of up to 256 KiB, each of chunks of up to 128 KiB.
A chunk is either one entropy-coded array or an LZ chunk: four entropy-coded arrays
(literals, commands, packed offsets, packed lengths) and a bit stream with the rest of the
offsets and lengths, read from both ends at once. An entropy-coded array is stored, Huffman
coded (in three or six interleaved bit streams), run-length coded, or several arrays joined.

The format has three more features that nothing in the game uses, and so they could not be
checked and are left out: tANS coded arrays, arrays interleaved from pieces of others, and
scaled offsets. decompress(data, size) returns the bytes; input that is bad or uses one of
those raises ValueError.
"""

from collections.abc import Sequence
from operator import itemgetter

import numpy as np

Buffer = bytes | bytearray


class _Bits:
    """Reads bits from a byte string, most significant first. Past the end it reads zeros."""

    __slots__ = ('data', 'pos')

    def __init__(self, data: Buffer) -> None:
        self.data = data + bytes(16)
        self.pos = 0

    def peek(self, n: int) -> int:
        p = self.pos
        v = int.from_bytes(self.data[p >> 3 : (p >> 3) + 8], 'big')
        return (v >> (64 - (p & 7) - n)) & ((1 << n) - 1)

    def read(self, n: int) -> int:
        p = self.pos
        self.pos = p + n
        v = int.from_bytes(self.data[p >> 3 : (p >> 3) + 8], 'big')
        return (v >> (64 - (p & 7) - n)) & ((1 << n) - 1)

    def zeros(self) -> int:
        """The number of zero bits before the next one bit, which must come within 32."""
        n = 32 - self.peek(32).bit_length()
        if n > 24:
            raise ValueError('bad bit stream')
        return n

    def gamma(self) -> int:
        n = self.zeros() + 1
        return self.read(2 * n) - 1

    def unary(self, count: int) -> list[int]:
        """count numbers, each stored as that many zero bits and a one."""
        start = self.pos >> 3
        size = 64
        while True:
            chunk = self.data[start : start + size]
            text = bin(int.from_bytes(chunk, 'big') | (1 << (8 * len(chunk))))[3 + (self.pos & 7) :]
            parts = text.split('1', count)
            if len(parts) > count:
                break
            if start + size >= len(self.data):
                raise ValueError('bad bit stream')
            size *= 4
        values = [len(x) for x in parts[:count]]
        self.pos += sum(values) + count
        return values

    def bytes_used(self) -> int:
        return (self.pos + 7) >> 3


# -- Huffman ----------------------------------------------------------------------------

_REVERSED = itemgetter(*[int('{:011b}'.format(i)[::-1], 2) for i in range(2048)])


def _fluff(b: _Bits, symbols: int) -> int:
    """The number of values that describe which symbols are in use (a truncated binary code)."""
    if symbols == 256:
        return 0
    x = 2 * min(257 - symbols, symbols)
    y = (x - 1).bit_length()
    v = b.peek(y)
    z = (1 << y) - x
    if (v >> 1) >= z:
        b.pos += y
        return v - z
    b.pos += y - 1
    return v >> 1


def _ranges(b: _Bits, symbols: int, fluff: int, widths: Sequence[int]) -> list[tuple[int, int]]:
    """The runs of symbols in use, as (first symbol, count)."""
    at = used = w = 0
    if fluff & 1:
        v = widths[0]
        w = 1
        if v >= 8:
            raise ValueError('bad symbol ranges')
        at = b.read(v + 1) + (1 << (v + 1)) - 1
    out = []
    for i in range(fluff >> 1):
        v, s = widths[w + 2 * i], widths[w + 2 * i + 1]
        if v >= 9 or s >= 8:
            raise ValueError('bad symbol ranges')
        count = b.read(v) + (1 << v)
        space = b.read(s + 1) + (1 << (s + 1)) - 1
        out.append((at, count))
        used += count
        at += count + space
    if at >= 256 or used >= symbols or at + symbols - used > 256:
        raise ValueError('bad symbol ranges')
    out.append((at, symbols - used))
    return out


def _lengths_old(b: _Bits, by_length: list[list[int]]) -> int:
    if not b.read(1):  # a list of (symbol, length)
        symbols = b.read(8)
        if symbols == 0:
            raise ValueError('bad code lengths')
        if symbols == 1:
            by_length[1].append(b.read(8))
            return 1
        width = b.read(3)
        if width > 4:
            raise ValueError('bad code lengths')
        for _ in range(symbols):
            sym = b.read(8)
            length = b.read(width) + 1
            if length > 11:
                raise ValueError('bad code lengths')
            by_length[length].append(sym)
        return symbols
    forced = b.read(2)
    sym = symbols = 0
    average = 32
    skip = b.read(1)
    while True:
        if not skip:
            sym += b.gamma()
            if sym >= 256:
                break
        skip = 0
        n = b.gamma()
        if sym + n > 256:
            raise ValueError('bad code lengths')
        symbols += n
        for _ in range(n):
            lz = b.zeros()
            v = b.read(lz + forced + 1) + ((lz - 1) << forced)
            length = (-(v & 1) ^ (v >> 1)) + ((average + 2) >> 2)
            if not 1 <= length <= 11:
                raise ValueError('bad code lengths')
            average = length + ((3 * average + 2) >> 2)
            by_length[length].append(sym)
            sym += 1
        if sym == 256:
            break
    if sym != 256 or symbols < 2:
        raise ValueError('bad code lengths')
    return symbols


def _lengths_new(b: _Bits, by_length: list[list[int]]) -> int:
    forced = b.read(2)
    symbols = b.read(8) + 1
    fluff = _fluff(b, symbols)
    values = b.unary(symbols + fluff)
    if forced:
        for i in range(symbols):
            values[i] = (values[i] << forced) | b.read(forced)
    total = 0x1E
    for i in range(symbols):
        v = values[i]
        v = -(v & 1) ^ (v >> 1)
        values[i] = v + (total >> 2) + 1
        if not 1 <= values[i] <= 11:
            raise ValueError('bad code lengths')
        total += v
    i = 0
    for first, count in _ranges(b, symbols, fluff, values[symbols:]):
        for sym in range(first, first + count):
            by_length[values[i]].append(sym)
            i += 1
    return symbols


def _huffman_stream(data: Buffer, count: int, lengths: bytes, symbols: bytes) -> bytearray:
    """Decodes count symbols from a bit stream that is read least significant bit first."""
    out = bytearray(count)
    acc = have = at = 0
    for i in range(count):
        if have < 11:
            acc |= int.from_bytes(data[at : at + 32], 'little') << have
            at += 32
            have += 256
        k = acc & 2047
        n = lengths[k]
        acc >>= n
        have -= n
        out[i] = symbols[k]
    return out


def _huffman_three(first: Buffer, rest: Buffer, size: int, lengths: bytes, symbols: bytes) -> bytearray:
    """Three interleaved streams: one over `first`, and two over `rest` from either end."""
    out = bytearray(size)
    out[0::3] = _huffman_stream(first, len(range(0, size, 3)), lengths, symbols)
    out[1::3] = _huffman_stream(rest[::-1], len(range(1, size, 3)), lengths, symbols)
    out[2::3] = _huffman_stream(rest, len(range(2, size, 3)), lengths, symbols)
    return out


def _huffman(src: Buffer, size: int, six: bool) -> Buffer:
    b = _Bits(src)
    by_length: list[list[int]] = [[] for _ in range(12)]
    if not b.read(1):
        count = _lengths_old(b, by_length)
    elif not b.read(1):
        count = _lengths_new(b, by_length)
    else:
        raise ValueError('bad Huffman table')
    p = b.bytes_used()
    if count == 1:
        return bytes(next(x for x in by_length if x)) * size
    lengths, symbols = bytearray(), bytearray()
    for n in range(1, 12):
        for sym in by_length[n]:
            lengths += bytes([n]) * (1 << (11 - n))
            symbols += bytes([sym]) * (1 << (11 - n))
    if len(lengths) != 2048:
        raise ValueError('bad Huffman table')
    length_table, symbol_table = bytes(_REVERSED(lengths)), bytes(_REVERSED(symbols))
    if not six:
        if p + 3 > len(src):
            raise ValueError('bad Huffman data')
        split = src[p] | src[p + 1] << 8
        return _huffman_three(src[p + 2 : p + 2 + split], src[p + 2 + split :], size, length_table, symbol_table)
    if p + 6 > len(src):
        raise ValueError('bad Huffman data')
    half = (size + 1) >> 1
    mid = p + 3 + int.from_bytes(src[p : p + 3], 'little')
    p += 3
    if mid + 2 > len(src):
        raise ValueError('bad Huffman data')
    left = src[p] | src[p + 1] << 8
    right = src[mid] | src[mid + 1] << 8
    return _huffman_three(
        src[p + 2 : p + 2 + left], src[p + 2 + left : mid], half, length_table, symbol_table
    ) + _huffman_three(src[mid + 2 : mid + 2 + right], src[mid + 2 + right :], size - half, length_table, symbol_table)


# -- the other kinds of array ------------------------------------------------------------


def _rle(src: Buffer, size: int) -> Buffer:
    if len(src) <= 1:
        if not src:
            raise ValueError('bad run-length data')
        return src * size
    if src[0]:  # the start of the commands is itself an array
        head, n = _array(src, 0, len(src))
        cmd: Buffer = bytes(head) + src[n:]
    else:
        cmd = src[1:]
    out = bytearray()
    p, end = 0, len(cmd)
    fill: Buffer = b'\0'
    while p < end:
        c = cmd[end - 1]
        if c == 0 or c >= 0x30:
            end -= 1
            copy, run = ~c & 15, c >> 4
        elif c >= 0x10:
            v = (cmd[end - 2] | c << 8) - 4096
            end -= 2
            copy, run = v & 63, v >> 6
        elif c == 1:
            fill = cmd[p : p + 1]
            p += 1
            end -= 1
            continue
        elif c >= 9:
            copy, run = 0, ((cmd[end - 2] | c << 8) - 0x8FF) * 128
            end -= 2
        else:
            copy, run = ((cmd[end - 2] | c << 8) - 511) * 64, 0
            end -= 2
        if copy > end - p:
            raise ValueError('bad run-length data')
        out += cmd[p : p + copy]
        p += copy
        out += fill * run
    if p != end or len(out) != size:
        raise ValueError('bad run-length data')
    return out


def _compound(src: Buffer, size: int) -> bytearray:
    count = src[0] & 0x7F
    if len(src) < 6 or count < 2:
        raise ValueError('bad array')
    if src[0] & 0x80:
        raise ValueError('array of interleaved pieces, which is not supported')
    out = bytearray()
    p = 1
    for _ in range(count):
        data, p = _array(src, p, len(src))
        out += data
    if len(out) != size or p != len(src):
        raise ValueError('bad array')
    return out


def _array(src: Buffer, p: int, end: int) -> tuple[Buffer, int]:
    """Decodes the entropy-coded array at p. Returns (its bytes, the position after it)."""
    if end - p < 2:
        raise ValueError('truncated array')
    first = src[p]
    kind = (first >> 4) & 7
    if kind == 0:  # stored
        if first >= 0x80:
            size = (first << 8 | src[p + 1]) & 0xFFF
            p += 2
        else:
            size = int.from_bytes(src[p : p + 3], 'big')
            if size & ~0x3FFFF:
                raise ValueError('bad array')
            p += 3
        if end - p < size:
            raise ValueError('truncated array')
        return src[p : p + size], p + size
    if first >= 0x80:
        v = int.from_bytes(src[p : p + 3], 'big')
        packed = v & 0x3FF
        size = packed + ((v >> 10) & 0x3FF) + 1
        p += 3
    else:
        v = int.from_bytes(src[p + 1 : p + 5], 'big')
        packed = v & 0x3FFFF
        size = (((v >> 18) | first << 14) & 0x3FFFF) + 1
        if packed >= size:
            raise ValueError('bad array')
        p += 5
    if end - p < packed:
        raise ValueError('truncated array')
    body = src[p : p + packed]
    if kind == 2 or kind == 4:
        out = _huffman(body, size, kind == 4)
    elif kind == 1:
        raise ValueError('tANS coded array, which is not supported')
    elif kind == 3:
        out = _rle(body, size)
    elif kind == 5:
        out = _compound(body, size)
    else:
        raise ValueError('unknown kind of array %d' % kind)
    return out, p + packed


# -- LZ ---------------------------------------------------------------------------------


def _offsets_and_lengths(src: Buffer, packed_offsets: Buffer, packed_lengths: Buffer) -> tuple[list[int], list[int]]:
    """Completes the offsets (as distances back) and lengths from the bit stream."""
    forward, backward = _Bits(src), _Bits(src[::-1])
    n = backward.zeros()
    if n > 18:
        raise ValueError('bad length count')
    backward.pos += n
    long_count = backward.read(n + 1) - 1
    offsets = []
    for i, v in enumerate(packed_offsets):
        b = backward if i & 1 else forward
        if v < 0xF0:
            n = (v >> 4) + 4
            offsets.append((((1 << n) | b.read(n)) << 4) + (v & 15) - 248)
        else:
            n = v - 0xF0 + 4
            high = ((1 << n) | b.read(n)) << 12
            offsets.append(8322816 + high + b.read(12))
    long_lengths = []
    for i in range(long_count):
        b = backward if i & 1 else forward
        n = b.zeros()
        if n > 12:
            raise ValueError('bad length')
        b.pos += n
        long_lengths.append(b.read(n + 7) - 64 + 255)
    if forward.bytes_used() + backward.bytes_used() != len(src):
        raise ValueError('bad offsets and lengths')
    longs = iter(long_lengths)
    try:
        lengths = [(next(longs) if v == 255 else v) + 3 for v in packed_lengths]
    except StopIteration:
        raise ValueError('bad lengths') from None
    if next(longs, None) is not None:
        raise ValueError('bad lengths')
    return offsets, lengths


def _add_literals(dst: bytearray, literals: bytes, back: int) -> None:
    """Appends literals that are stored as their difference from the bytes `back` behind."""
    n = len(literals)
    if n < 24:
        at = len(dst) - back
        for i in range(n):
            dst.append((literals[i] + dst[at + i]) & 255)
        return
    done = 0
    while done < n:  # at most `back` at a time, as each piece reads what the last wrote
        step = min(back, n - done)
        at = len(dst) - back
        dst += (np.frombuffer(literals, np.uint8, step, done) + np.frombuffer(dst, np.uint8, step, at)).tobytes()
        done += step


def _lz_chunk(src: Buffer, p: int, end: int, dst: bytearray, size: int, mode: int) -> None:
    """Appends the size bytes of the LZ chunk at src[p:end] to dst."""
    if mode > 1:
        raise ValueError('unknown LZ mode %d' % mode)
    if end - p < 13:
        raise ValueError('truncated LZ chunk')
    target = len(dst) + size
    if not dst:
        dst += src[p : p + 8]
        p += 8
    if src[p] & 0x80:
        raise ValueError('LZ chunk with excess bytes')
    literals, p = _array(src, p, end)
    commands, p = _array(src, p, end)
    if end - p < 3:
        raise ValueError('truncated LZ chunk')
    if src[p] & 0x80:
        raise ValueError('LZ chunk with scaled offsets, which is not supported')
    packed_offsets, p = _array(src, p, end)
    packed_lengths, p = _array(src, p, end)
    found_offsets, found_lengths = _offsets_and_lengths(src[p:end], packed_offsets, packed_lengths)

    literals = bytes(literals)
    offsets, lengths = iter(found_offsets), iter(found_lengths)
    recent0 = recent1 = recent2 = 8
    at = 0  # in literals
    try:
        for f in commands:
            n = f & 3
            if n == 3:
                n = next(lengths)
            if n:
                if mode:
                    dst += literals[at : at + n]
                else:
                    _add_literals(dst, literals[at : at + n], recent0)
                at += n
            which = f >> 6
            if which == 0:
                back = recent0
            elif which == 1:
                back = recent1
                recent1 = recent0
            elif which == 2:
                back = recent2
                recent2 = recent1
                recent1 = recent0
            else:
                back = next(offsets)
                recent2 = recent1
                recent1 = recent0
            recent0 = back
            n = (f >> 2) & 15
            n = 14 + next(lengths) if n == 15 else n + 2
            start = len(dst) - back
            if start < 0 or back <= 0:
                raise ValueError('bad offset')
            if back >= n:
                dst += dst[start : start + n]
            else:
                dst += (dst[start:] * (n // back + 1))[:n]
    except StopIteration:
        raise ValueError('bad LZ chunk') from None
    rest = target - len(dst)
    if rest < 0 or rest != len(literals) - at or next(offsets, None) is not None or next(lengths, None) is not None:
        raise ValueError('bad LZ chunk')
    if mode:
        dst += literals[at:]
    else:
        _add_literals(dst, literals[at:], recent0)


def _quantum(src: Buffer, p: int, end: int, dst: bytearray, size: int) -> None:
    target = len(dst) + size
    while len(dst) < target:
        count = min(0x20000, target - len(dst))
        if end - p < 4:
            raise ValueError('truncated chunk')
        header = int.from_bytes(src[p : p + 3], 'big')
        if not header & 0x800000:  # no matches: one array
            data, p = _array(src, p, end)
            if len(data) != count:
                raise ValueError('bad chunk')
            dst += data
            continue
        p += 3
        used = header & 0x7FFFF
        mode = (header >> 19) & 15
        if end - p < used:
            raise ValueError('truncated chunk')
        if used < count:
            _lz_chunk(src, p, p + used, dst, count, mode)
        elif used == count and mode == 0:
            dst += src[p : p + count]
        else:
            raise ValueError('bad chunk')
        p += used
    if p != end:
        raise ValueError('bad quantum')


def decompress(data: Buffer, size: int) -> bytes:
    """Decompresses a Kraken buffer that holds `size` bytes."""
    src = bytes(data)
    dst = bytearray()
    p = 0
    stored = checksums = 0
    try:
        while len(dst) < size:
            if not len(dst) & 0x3FFFF:
                first, second = src[p], src[p + 1]
                if first & 0x3F != 0x0C or second & 0x7F != 6:
                    raise ValueError('not Kraken data')
                stored, checksums = first & 0x40, second & 0x80
                p += 2
            left = min(0x40000, size - len(dst))
            if stored:
                dst += src[p : p + left]
                p += left
                continue
            header = int.from_bytes(src[p : p + 3], 'big')
            packed = header & 0x3FFFF
            if packed == 0x3FFFF:
                if header >> 18 != 1:
                    raise ValueError('bad quantum')
                dst += src[p + 3 : p + 4] * left
                p += 4
                continue
            packed += 1
            p += 6 if checksums else 3
            if packed > left or p + packed > len(src):
                raise ValueError('bad quantum')
            if packed == left:
                dst += src[p : p + left]
            else:
                _quantum(src, p, p + packed, dst, left)
            p += packed
    except IndexError:
        raise ValueError('truncated Kraken data') from None
    if len(dst) != size or p != len(src):
        raise ValueError('bad Kraken data')
    return bytes(dst)
