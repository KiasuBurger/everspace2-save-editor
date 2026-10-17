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

"""Reads files out of the game's .pak, whose index is encrypted.

The game's data files (DataFiles/*.CSV) are in pakchunk0-Windows.pak. A .pak is the files
one after another, an index of them, and a footer that says where the index is. Here the
index is encrypted with AES-256 and the files are not; they are compressed in blocks with
Oodle (see kraken.py). The key is in the game's executable, which puts it together from
eight 32-bit constants in consecutive instructions, so it is found by trying every such run
of constants on the first block of the index, which has to start with the mount point.

    files = read_files(game, 'ES2/Content/DataFiles/')      {path below that: bytes}

Only what this game's .pak needs is here: version 11 of the format, and decryption only.
"""

import hashlib
import os
import struct
from collections.abc import Callable, Iterator
from typing import Self

import numpy as np

from . import kraken

PAK = os.path.join('ES2', 'Content', 'Paks', 'pakchunk0-Windows.pak')
EXECUTABLE = os.path.join('ES2', 'Binaries', 'Win64', 'ES2-Win64-Shipping.exe')
MAGIC = 0x5A6F12E1
MOUNT = b'../../../'


# -- AES-256, decryption only ------------------------------------------------------------


def _multiply(a: int, b: int) -> int:
    """In GF(2^8), as AES has it."""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = (a << 1) ^ 0x11B if a & 0x80 else a << 1
        b >>= 1
    return r


def _tables() -> tuple[list[int], ...]:
    sbox = [0] * 256
    p = q = 1
    while True:  # p runs through every non-zero value, q through their inverses
        p = _multiply(p, 3)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q
        for shift in (1, 2, 3, 4):
            x ^= (q << shift | q >> (8 - shift)) & 0xFF
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inverse = [0] * 256
    for i, v in enumerate(sbox):
        inverse[v] = i
    # a round of decryption on one byte of a column, for each of the four places it can be
    t0 = [_multiply(v, 14) | _multiply(v, 9) << 8 | _multiply(v, 13) << 16 | _multiply(v, 11) << 24 for v in inverse]
    rotated = [[(v << s | v >> (32 - s)) & 0xFFFFFFFF for v in t0] for s in (8, 16, 24)]
    return sbox, inverse, t0, *rotated


_SBOX, _INVERSE, _T0, _T1, _T2, _T3 = _tables()


def _substitute(word: int) -> int:
    return _SBOX[word & 255] | _SBOX[word >> 8 & 255] << 8 | _SBOX[word >> 16 & 255] << 16 | _SBOX[word >> 24] << 24


def aes_key(key: bytes) -> list[list[int]]:
    """The round keys for decrypting with a 32-byte key, as aes_decrypt takes them."""
    w = list(struct.unpack('<8I', key))
    constant = 1
    for i in range(8, 60):
        t = w[i - 1]
        if i % 8 == 0:
            t = _substitute(t >> 8 | (t & 0xFF) << 24) ^ constant
            constant = _multiply(constant, 2)
        elif i % 8 == 4:
            t = _substitute(t)
        w.append(w[i - 8] ^ t)
    rounds = [w[56:60]]
    for r in range(13, 0, -1):
        rounds.append(
            [
                _T0[_SBOX[v & 255]] ^ _T1[_SBOX[v >> 8 & 255]] ^ _T2[_SBOX[v >> 16 & 255]] ^ _T3[_SBOX[v >> 24]]
                for v in w[4 * r : 4 * r + 4]
            ]
        )
    rounds.append(w[0:4])
    return rounds


def aes_decrypt(rounds: list[list[int]], data: bytes) -> bytes:
    """Decrypts whole 16-byte blocks, each on its own (ECB), which is how a .pak is encrypted."""
    words = struct.unpack('<%dI' % (len(data) // 4), data)
    first, middle, last = rounds[0], rounds[1:14], rounds[14]
    t0, t1, t2, t3, inv = _T0, _T1, _T2, _T3, _INVERSE
    out = []
    for i in range(0, len(words), 4):
        a, b, c, d = words[i] ^ first[0], words[i + 1] ^ first[1], words[i + 2] ^ first[2], words[i + 3] ^ first[3]
        for k in middle:
            a, b, c, d = (
                t0[a & 255] ^ t1[d >> 8 & 255] ^ t2[c >> 16 & 255] ^ t3[b >> 24] ^ k[0],
                t0[b & 255] ^ t1[a >> 8 & 255] ^ t2[d >> 16 & 255] ^ t3[c >> 24] ^ k[1],
                t0[c & 255] ^ t1[b >> 8 & 255] ^ t2[a >> 16 & 255] ^ t3[d >> 24] ^ k[2],
                t0[d & 255] ^ t1[c >> 8 & 255] ^ t2[b >> 16 & 255] ^ t3[a >> 24] ^ k[3],
            )
        out += [
            (inv[a & 255] | inv[d >> 8 & 255] << 8 | inv[c >> 16 & 255] << 16 | inv[b >> 24] << 24) ^ last[0],
            (inv[b & 255] | inv[a >> 8 & 255] << 8 | inv[d >> 16 & 255] << 16 | inv[c >> 24] << 24) ^ last[1],
            (inv[c & 255] | inv[b >> 8 & 255] << 8 | inv[a >> 16 & 255] << 16 | inv[d >> 24] << 24) ^ last[2],
            (inv[d & 255] | inv[c >> 8 & 255] << 8 | inv[b >> 16 & 255] << 16 | inv[a >> 24] << 24) ^ last[3],
        ]
    return struct.pack('<%dI' % len(out), *out)


# -- the key -----------------------------------------------------------------------------

# where the instructions that carry the key are in the executable of the last version of the
# game, which is tried before looking through all of it
KNOWN_KEY_OFFSET = 0x603BF9F


def _keys(code: bytes) -> Iterator[tuple[int, bytes]]:
    """The keys that code could be putting together, each with where its first constant is
    stored: the constants of every eight instructions close together that store one."""
    exe = np.frombuffer(code, dtype=np.uint8)
    # C7 /0: mov dword [..], imm32, wherever one could start; most of these are not
    # instructions at all, but the eight that carry the key are found among them
    at = np.flatnonzero(exe[:-12] == 0xC7)
    modrm = exe[at + 1]
    mode, register, base = modrm >> 6, (modrm >> 3) & 7, modrm & 7
    keep = (register == 0) & (mode != 3)
    at, mode, base = at[keep], mode[keep], base[keep]
    displacement = np.where(mode == 1, 1, np.where((mode == 2) | (base == 5), 4, 0))
    constant = at + 2 + (base == 4) + displacement
    constants = (
        exe[constant].astype(np.uint32)
        | exe[constant + 1].astype(np.uint32) << 8
        | exe[constant + 2].astype(np.uint32) << 16
        | exe[constant + 3].astype(np.uint32) << 24
    )
    for i in np.flatnonzero(at[7:] - at[:-7] <= 160):
        yield int(at[i]), constants[i : i + 8].tobytes()


def find_key(executable: str, block: bytes) -> bytes:
    """The key that decrypts block, the first 16 bytes of an index, to the start of one: found
    among the constants that the executable at the given path stores with consecutive
    instructions. Raises ValueError if it is not there."""

    def fits(key: bytes) -> bool:
        plain = aes_decrypt(aes_key(key), block)
        return plain[1:4] == b'\0\0\0' and plain[4 : 4 + len(MOUNT)] == MOUNT

    with open(executable, 'rb') as f:
        f.seek(KNOWN_KEY_OFFSET)
        for at, key in _keys(f.read(256)):
            if at == 0 and fits(key):
                return key
        f.seek(0)
        code = f.read()
    for _, key in _keys(code):
        if fits(key):
            return key
    raise ValueError("the key of the game's .pak was not found in %s" % executable)


# -- the .pak ----------------------------------------------------------------------------


def _string(d: bytes, p: int) -> tuple[str, int]:
    (n,) = struct.unpack_from('<i', d, p)
    p += 4
    if n < 0:
        return d[p : p - 2 * n - 2].decode('utf-16-le'), p - 2 * n
    return d[p : p + n - 1].decode('utf-8'), p + n


class Pak:
    """An open .pak: `files` is {path: where its entry is}, and read(path) gives its bytes."""

    def __init__(self, path: str, executable: str) -> None:
        self.path = path
        self.file = open(path, 'rb')  # noqa: SIM115 (kept open until close())
        try:
            self._read_index(executable)
        except struct.error, IndexError, UnicodeDecodeError:
            self.file.close()
            raise ValueError('%s is damaged' % path) from None
        except BaseException:
            self.file.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.file.close()

    def _section(self, offset: int, size: int, digest: bytes) -> bytes:
        self.file.seek(offset)
        data = self.file.read(size)
        if self.rounds:
            data = aes_decrypt(self.rounds, data)
        if hashlib.sha1(data).digest() != digest:
            raise ValueError('the index of %s does not decrypt to what it should' % self.path)
        return data

    def _read_index(self, executable: str) -> None:
        f = self.file
        f.seek(-221, 2)
        footer = f.read()
        encrypted, magic, version, offset, size = struct.unpack_from('<BIIQQ', footer, 16)
        if magic != MAGIC or version != 11:
            raise ValueError('%s is not a .pak of the version this reads' % self.path)
        self.methods = [footer[61 + 32 * i : 93 + 32 * i].rstrip(b'\0') for i in range(5)]
        self.rounds: list[list[int]] | None = None
        if encrypted:
            f.seek(offset)
            self.rounds = aes_key(find_key(executable, f.read(16)))
        index = self._section(offset, size, footer[41:61])
        mount, p = _string(index, 0)
        (hashed,) = struct.unpack_from('<I', index, p + 12)  # after the number of files and a seed
        p += 16 + (36 if hashed else 0)
        (listed,) = struct.unpack_from('<I', index, p)
        if not listed:
            raise ValueError('%s does not list its files by name' % self.path)
        offset, size = struct.unpack_from('<QQ', index, p + 4)
        digest = index[p + 20 : p + 40]
        (size_of_entries,) = struct.unpack_from('<I', index, p + 40)
        self.entries = index[p + 44 : p + 44 + size_of_entries]
        d = self._section(offset, size, digest)
        self.files: dict[str, int] = {}
        (folders,) = struct.unpack_from('<I', d, 0)
        p = 4
        for _ in range(folders):
            folder, p = _string(d, p)
            (n,) = struct.unpack_from('<I', d, p)
            p += 4
            for _ in range(n):
                name, p = _string(d, p)
                (self.files[(mount + folder.lstrip('/') + name)[len(MOUNT) :]],) = struct.unpack_from('<i', d, p)
                p += 4

    def read(self, name: str) -> bytes:
        try:
            return self._read(self.files[name])
        except struct.error, IndexError:
            raise ValueError('%s is damaged' % self.path) from None

    def _read(self, p: int) -> bytes:
        e = self.entries
        (bits,) = struct.unpack_from('<I', e, p)
        p += 4
        method, encrypted, blocks, block_size = (bits >> 23) & 0x3F, bits & 1 << 22, (bits >> 6) & 0xFFFF, bits & 0x3F
        if block_size == 0x3F:
            (block_size,) = struct.unpack_from('<I', e, p)
            p += 4
        else:
            block_size <<= 11
        fields: list[int] = []
        for short in (bits & 1 << 31, bits & 1 << 30, bits & 1 << 29 if method else None):
            if short is None:
                fields.append(fields[-1])
                continue
            fields.append(struct.unpack_from('<I' if short else '<Q', e, p)[0])
            p += 4 if short else 8
        offset, size, packed = fields
        # the file's data follows a copy of its entry, which is longer for its list of blocks
        at = offset + 53 + (4 + 16 * blocks if method else 0)
        if not method:
            return self._data(at, size, encrypted)
        if self.methods[method - 1] != b'Oodle':
            raise ValueError('%s is compressed with %s' % (self.path, self.methods[method - 1].decode()))
        sizes = [packed] if blocks == 1 and not encrypted else struct.unpack_from('<%dI' % blocks, e, p)
        out = bytearray()
        for packed in sizes:
            out += kraken.decompress(self._data(at, packed, encrypted), min(block_size or size, size - len(out)))
            at += (packed + 15) & ~15 if encrypted else packed
        return bytes(out)

    def _data(self, at: int, size: int, encrypted: int) -> bytes:
        self.file.seek(at)
        if not encrypted:
            return self.file.read(size)
        if not self.rounds:
            raise ValueError('%s has an encrypted file and an index that is not' % self.path)
        return aes_decrypt(self.rounds, self.file.read((size + 15) & ~15))[:size]


def read_files(game: str, prefix: str, wanted: Callable[[str], object] | None = None) -> dict[str, bytes]:
    """The files of the game's .pak whose paths start with prefix, those that wanted accepts
    by the rest of the path if it is given, as {the rest of the path: bytes}. Raises OSError,
    or ValueError if the game's files are not as expected."""
    with Pak(os.path.join(game, PAK), os.path.join(game, EXECUTABLE)) as pak:
        names = [name[len(prefix) :] for name in pak.files if name.startswith(prefix)]
        return {name: pak.read(prefix + name) for name in names if not wanted or wanted(name)}
