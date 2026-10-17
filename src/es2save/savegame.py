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

"""Everspace 2 savegame codec: decode a save to a JSON-compatible tree and encode it back.

Container:  u32 magic 0x00DEC0DE | u32 version (2) | u32 uncompressed size | u32 compressed size | zlib stream
Payload:    Unreal Engine GVAS (UE 4.26 through 5.6), root class /Script/ES2.PlayerData
            *_PREVIEW_.sav and MetaData.sav are bare GVAS without the container.

Tree model (plain JSON types, see FORMAT.md):
    property list   {name: {"type": "<type name>", "value": ...}, ...} in file order; a static
                    array element with index n > 0 is keyed "name[n]"
    Array / Set     list (raw byte arrays are a hex string)
    Map             list of [key, value] pairs
    Struct          nested property list, or a native value (list of numbers, dict, ...)

Entry points: load / loads / dump / dumps for whole files, parse_gvas / build_gvas for the
uncompressed payload, unpack_container / pack_container for the compression layer.
"""

import os
import re
import struct
import zlib
from collections.abc import Callable, Collection
from typing import Any

MAGIC = 0x00DEC0DE

# EUnrealEngineObjectUE5Version values that change the wire format
UE5_LARGE_WORLD_COORDINATES = 1004
UE5_FTOPLEVELASSETPATH = 1007
UE5_PROPERTY_TAG_EXTENSION = 1011
UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME = 1012

ZERO_GUID = '00' * 16

# the tree model, in the terms of the module's description
Property = dict[str, Any]  # {"type": ..., "value": ...} and the optional keys beside them
Properties = dict[str, Property]  # a property list
Save = dict[str, Any]  # {"header": ..., "properties": ..., "container": ...}
StrPath = str | os.PathLike[str]


def unpack_container(data: bytes) -> bytes:
    """Returns the raw GVAS bytes for either a wrapped or a bare save."""
    if data[:4] == b'GVAS':
        return data
    if len(data) < 16:
        raise ParseError('truncated save: %d bytes' % len(data))
    magic, version, usize, csize = struct.unpack_from('<IIII', data, 0)
    if magic != MAGIC:
        raise ValueError('not an ES2 save (magic %08x)' % magic)
    if version != 2:
        raise ValueError('unsupported container version %d' % version)
    try:
        out = zlib.decompress(data[16 : 16 + csize])
    except zlib.error as e:
        raise ParseError('damaged save: %s' % e) from None
    if len(out) != usize:
        raise ValueError('size mismatch: header %d, got %d' % (usize, len(out)))
    return out


def pack_container(gvas: bytes, level: int = 6) -> bytes:
    comp = zlib.compress(gvas, level)
    return struct.pack('<IIII', MAGIC, 2, len(gvas), len(comp)) + comp


class ParseError(Exception):
    pass


class EncodeError(Exception):
    pass


# ---------------------------------------------------------------- primitive IO


class Reader:
    def __init__(self, data: bytes, pos: int = 0, ue5: int = 0) -> None:
        self.d = data
        self.p = pos
        self.ue5 = ue5  # UE5 object version of the archive being read (0 for UE4)

    def sub(self, data: bytes, ue5: int | None = None) -> Reader:
        return Reader(data, 0, self.ue5 if ue5 is None else ue5)

    def u8(self) -> int:
        if self.p >= len(self.d):
            raise ParseError('eof')
        v = self.d[self.p]
        self.p += 1
        return v

    def _f(self, fmt: str, n: int) -> Any:
        if self.p + n > len(self.d):
            raise ParseError('eof')
        v = struct.unpack_from(fmt, self.d, self.p)[0]
        self.p += n
        return v

    def i8(self) -> int:
        return self._f('<b', 1)

    def i16(self) -> int:
        return self._f('<h', 2)

    def u16(self) -> int:
        return self._f('<H', 2)

    def i32(self) -> int:
        return self._f('<i', 4)

    def u32(self) -> int:
        return self._f('<I', 4)

    def i64(self) -> int:
        return self._f('<q', 8)

    def u64(self) -> int:
        return self._f('<Q', 8)

    def f32(self) -> float:
        return self._f('<f', 4)

    def f64(self) -> float:
        return self._f('<d', 8)

    def raw(self, n: int) -> bytes:
        v = self.d[self.p : self.p + n]
        if len(v) != n:
            raise ParseError('eof')
        self.p += n
        return v

    def guid(self) -> str:
        return self.raw(16).hex()

    def fstring(self) -> str:
        n = self.i32()
        if n == 0:
            return ''
        if n == 1:
            self.raw(1)
            return '\0'  # an allocated-but-empty FString; kept distinct from length 0
        if n > 0:
            if n > 1 << 24:
                raise ParseError('bad string length %d at %#x' % (n, self.p - 4))
            s = self.raw(n)
            return s[:-1].decode('latin-1')
        n = -n
        if n > 1 << 24:
            raise ParseError('bad string length -%d at %#x' % (n, self.p - 4))
        s = self.raw(n * 2)
        return s[:-2].decode('utf-16-le', 'surrogatepass')

    def real(self) -> float:
        """FVector component: double with large world coordinates, float before."""
        return self.f64() if self.ue5 >= UE5_LARGE_WORLD_COORDINATES else self.f32()


class Writer:
    def __init__(self, ue5: int = 0) -> None:
        self.b = bytearray()
        self.ue5 = ue5

    def sub(self, ue5: int | None = None) -> Writer:
        return Writer(self.ue5 if ue5 is None else ue5)

    def _f(self, fmt: str, v: object) -> None:
        try:
            self.b += struct.pack(fmt, v)
        except (struct.error, TypeError) as e:
            raise EncodeError('cannot encode %r as %s: %s' % (v, fmt, e)) from None

    def u8(self, v: int) -> None:
        self._f('<B', v)

    def i8(self, v: int) -> None:
        self._f('<b', v)

    def i16(self, v: int) -> None:
        self._f('<h', v)

    def u16(self, v: int) -> None:
        self._f('<H', v)

    def i32(self, v: int) -> None:
        self._f('<i', v)

    def u32(self, v: int) -> None:
        self._f('<I', v)

    def i64(self, v: int) -> None:
        self._f('<q', v)

    def u64(self, v: int) -> None:
        self._f('<Q', v)

    def f32(self, v: float) -> None:
        self._f('<f', v)

    def f64(self, v: float) -> None:
        self._f('<d', v)

    def raw(self, v: bytes | bytearray) -> None:
        self.b += v

    def guid(self, v: str) -> None:
        g = bytes.fromhex(v)
        if len(g) != 16:
            raise EncodeError('bad guid %r' % v)
        self.b += g

    def fstring(self, s: str) -> None:
        if not isinstance(s, str):
            raise EncodeError('expected a string, got %r' % (s,))
        if s == '':
            self.b += b'\0\0\0\0'
        elif s == '\0':
            self.b += b'\1\0\0\0\0'
        elif s.isascii():
            self.i32(len(s) + 1)
            self.b += s.encode('ascii') + b'\0'
        else:
            e = s.encode('utf-16-le', 'surrogatepass')
            self.i32(-(len(e) // 2 + 1))
            self.b += e + b'\0\0'

    def real(self, v: float) -> None:
        if self.ue5 >= UE5_LARGE_WORLD_COORDINATES:
            self.f64(v)
        else:
            self.f32(v)

    def patch_i32(self, pos: int, v: int) -> None:
        struct.pack_into('<i', self.b, pos, v)


# A codec is a (read(r), write(w, value)) pair; a field of a struct adds its name in front.
Codec = tuple[Callable[[Reader], Any], Callable[[Writer, Any], None]]
Field = tuple[str, Callable[[Reader], Any], Callable[[Writer, Any], None]]

# ---------------------------------------------------------------- type names


class TypeName:
    """FPropertyTypeName: a tree of (name, [params])."""

    __slots__ = ('hinted', 'name', 'params')

    def __init__(self, name: str, params: Collection[TypeName] = (), hinted: bool = False) -> None:
        self.name = name
        self.params = list(params)
        self.hinted = hinted  # struct type guessed from LEGACY_STRUCT_HINTS, not read from the file

    @classmethod
    def read(cls, r: Reader) -> TypeName:
        name = r.fstring()
        n = r.i32()
        if n < 0 or n > 16:
            raise ParseError('bad type param count %d at %#x' % (n, r.p - 4))
        return cls(name, [cls.read(r) for _ in range(n)])

    def write(self, w: Writer) -> None:
        w.fstring(self.name)
        w.i32(len(self.params))
        for p in self.params:
            p.write(w)

    def __str__(self) -> str:
        if not self.params:
            return self.name
        return '%s<%s>' % (self.name, ','.join(map(str, self.params)))

    _cache: dict[str, TypeName] = {}  # noqa: RUF012 (shared by design)
    _token = re.compile(r'[<>,]|[^<>,]+')

    @classmethod
    def parse(cls, s: str) -> TypeName:
        """Inverse of __str__."""
        t = cls._cache.get(s)
        if t is None:
            toks = cls._token.findall(s)
            t, i = cls._parse(toks, 0)
            if i != len(toks):
                raise EncodeError('bad type name %r' % s)
            cls._cache[s] = t
        return t

    @classmethod
    def _parse(cls, toks: list[str], i: int) -> tuple[TypeName, int]:
        if i >= len(toks) or toks[i] in '<>,':
            raise EncodeError('bad type name')
        t = cls(toks[i])
        i += 1
        if i < len(toks) and toks[i] == '<':
            while True:
                p, i = cls._parse(toks, i + 1)
                t.params.append(p)
                if i >= len(toks):
                    raise EncodeError('bad type name')
                if toks[i] == '>':
                    i += 1
                    break
                if toks[i] != ',':
                    raise EncodeError('bad type name')
        return t, i


# tag flags (EPropertyTagFlags)
F_ARRAY_INDEX = 0x01
F_GUID = 0x02
F_EXTENSIONS = 0x04
F_BINARY_OR_NATIVE = 0x08
F_BOOL_TRUE = 0x10
F_SKIPPED = 0x20
F_DERIVED = F_ARRAY_INDEX | F_GUID | F_BOOL_TRUE  # implied by the rest of the entry


def _is_legacy(a: Reader | Writer) -> bool:
    return a.ue5 < UE5_PROPERTY_TAG_COMPLETE_TYPE_NAME


# ---------------------------------------------------------------- native structs
# Each codec is a (read(r), write(w, value)) pair.


def _reals(n: int) -> Codec:
    def read(r: Reader) -> list[float]:
        return [r.real() for _ in range(n)]

    def write(w: Writer, v: Any) -> None:
        _expect_len(v, n)
        for x in v:
            w.real(x)

    return read, write


def _fixed(n: int, fmt: str, size: int) -> Codec:
    f = '<%d%s' % (n, fmt)

    def read(r: Reader) -> list[Any]:
        return list(struct.unpack_from(f, r.raw(n * size)))

    def write(w: Writer, v: Any) -> None:
        _expect_len(v, n)
        try:
            w.raw(struct.pack(f, *v))
        except struct.error as e:
            raise EncodeError('cannot encode %r: %s' % (v, e)) from None

    return read, write


def _expect_len(v: object, n: int) -> None:
    if not isinstance(v, list) or len(v) != n:
        raise EncodeError('expected a list of %d numbers, got %r' % (n, v))


def _scalar(rname: str) -> Codec:
    return getattr(Reader, rname), getattr(Writer, rname)


def _read_soft_path(r: Reader) -> dict[str, str]:
    if r.ue5 >= UE5_FTOPLEVELASSETPATH:
        # FTopLevelAssetPath (package name, asset name) + sub path string
        return {'package': r.fstring(), 'asset': r.fstring(), 'sub': r.fstring()}
    return {'path': r.fstring(), 'sub': r.fstring()}


def _write_soft_path(w: Writer, v: dict[str, str]) -> None:
    if w.ue5 >= UE5_FTOPLEVELASSETPATH:
        w.fstring(v['package'])
        w.fstring(v['asset'])
    else:
        w.fstring(v['path'])
    w.fstring(v['sub'])


# EVariantTypes id -> (name, codec)
VARIANT_CODECS = {
    0: ('empty', (lambda r: None, lambda w, v: None)),
    2: ('bool', (lambda r: bool(r.u32()), lambda w, v: w.u32(1 if v else 0))),
    8: ('double', _scalar('f64')),
    10: ('float', _scalar('f32')),
    11: ('guid', _scalar('guid')),
    12: ('int8', _scalar('i8')),
    13: ('int16', _scalar('i16')),
    14: ('int32', _scalar('i32')),
    15: ('int64', _scalar('i64')),
    19: ('name', _scalar('fstring')),
    21: ('quat', _reals(4)),
    23: ('rotator', _reals(3)),
    24: ('string', _scalar('fstring')),
    26: ('timespan', _scalar('i64')),
    29: ('uint8', _scalar('u8')),
    30: ('uint16', _scalar('u16')),
    31: ('uint32', _scalar('u32')),
    32: ('uint64', _scalar('u64')),
    33: ('vector', _reals(3)),
    34: ('vector2d', _reals(2)),
    35: ('vector4', _reals(4)),
    36: ('intpoint', _fixed(2, 'i', 4)),
    37: ('intvector', _fixed(3, 'i', 4)),
}
VARIANT_IDS = {name: (tid, codec) for tid, (name, codec) in VARIANT_CODECS.items()}


def _encode_variant_payload(a: Reader | Writer, v: dict[str, Any]) -> tuple[int, bytes]:
    """v is {'variant': name, 'value': x} or {'variant': id, 'raw': hex}."""
    if 'raw' in v:
        return v['variant'], bytes.fromhex(v['raw'])
    if v['variant'] not in VARIANT_IDS:
        raise EncodeError('unknown variant type %r' % (v['variant'],))
    tid, codec = VARIANT_IDS[v['variant']]
    w = Writer(a.ue5)
    codec[1](w, v['value'])
    return tid, bytes(w.b)


def _read_es_variant(r: Reader) -> dict[str, Any]:
    # FESVariant wraps an engine FVariant: i32 EVariantTypes, TArray<uint8> payload
    t = r.i32()
    n = r.i32()
    if n < 0 or n > 1 << 20:
        raise ParseError('bad ESVariant size %d at %#x' % (n, r.p))
    raw = r.raw(n)
    if t in VARIANT_CODECS:
        name, codec = VARIANT_CODECS[t]
        sub = r.sub(raw)
        try:
            out = {'variant': name, 'value': codec[0](sub)}
            # only keep the decoded form if it re-encodes to exactly the same bytes
            if sub.p == n and _encode_variant_payload(r, out) == (t, raw):
                return out
        except ParseError, EncodeError, struct.error, IndexError:
            pass
    return {'variant': t, 'raw': raw.hex()}


def _write_es_variant(w: Writer, v: dict[str, Any]) -> None:
    tid, payload = _encode_variant_payload(w, v)
    w.i32(tid)
    w.i32(len(payload))
    w.raw(payload)


def _read_map(r: Reader, read_key: Callable[[Reader], Any], read_val: Callable[[Reader], Any]) -> list[list[Any]]:
    if r.i32() != 0:
        raise ParseError('map with removed keys at %#x' % r.p)
    n = r.i32()
    if n < 0:
        raise ParseError('negative map count at %#x' % r.p)
    out = []
    for _ in range(n):
        k = read_key(r)
        out.append([k, read_val(r)])
    return out


def _write_map(
    w: Writer, v: Any, write_key: Callable[[Writer, Any], None], write_val: Callable[[Writer, Any], None]
) -> None:
    if not isinstance(v, list):
        raise EncodeError('expected a list of [key, value] pairs, got %s' % type(v).__name__)
    w.i32(0)
    w.i32(len(v))
    for pair in v:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise EncodeError('expected a [key, value] pair, got %r' % (pair,))
        write_key(w, pair[0])
        write_val(w, pair[1])


def _struct_array_type(struct_name: str) -> TypeName:
    return TypeName('ArrayProperty', [TypeName('StructProperty', [TypeName(struct_name)])])


# Structs written with UStruct::SerializeBin: property values in declaration order with no
# tags (nested structs are still tagged property lists). Each field is (name, read, write).
def _bin_struct(fields: list[Field]) -> Codec:
    def read(r: Reader) -> dict[str, Any]:
        return {name: rd(r) for name, rd, _ in fields}

    def write(w: Writer, v: dict[str, Any]) -> None:
        for name, _, wr in fields:
            if name not in v:
                raise EncodeError('missing field %r' % name)
            try:
                wr(w, v[name])
            except EncodeError as e:
                raise EncodeError('%s <- %s' % (e, name)) from None

    return read, write


def _field_props(name: str) -> Field:
    return name, lambda r: read_properties(r), lambda w, v: write_properties(w, v)


def _field_prop_map(name: str) -> Field:
    return (
        name,
        lambda r: _read_map(r, Reader.fstring, read_properties),
        lambda w, v: _write_map(w, v, Writer.fstring, write_properties),
    )


def _field_struct_array(name: str, struct_name: str) -> Field:
    return (
        name,
        lambda r: read_value(r, _struct_array_type(struct_name), in_container=True, name=name)[0],
        lambda w, v: write_value(w, _struct_array_type(struct_name), v, in_container=True, name=name),
    )


_location_state = _bin_struct(
    [
        ('GameVersion', Reader.fstring, Writer.fstring),
        _field_prop_map('SpawnGroupSaveStates'),  # ESpawnGroup -> FSpawnGroupSaveStateArray
        _field_prop_map('MissionGroupSaveStates'),  # FName -> FSpawnGroupSaveStateArray
        _field_struct_array('POISaveStates', 'POISpawnInfo'),
        (
            'ResourcesMined',
            lambda r: _read_map(r, Reader.fstring, Reader.i32),
            lambda w, v: _write_map(w, v, Writer.fstring, Writer.i32),
        ),
        ('AdditionalTimePassedSeconds', Reader.f64, Writer.f64),
        ('EnteredLocationTimestamp', Reader.f64, Writer.f64),
    ]
)

_station_instance = _bin_struct(
    [
        ('StationID', Reader.fstring, Writer.fstring),
        _field_props('ShopInventory'),  # FInventoryState
        _field_props('ShopRebuyInventory'),  # FInventoryState
        _field_struct_array('ShipsForSale', 'ShipDataState'),
        ('CreationTimeStamp', Reader.f64, Writer.f64),
    ]
)


def _buffer_wrapper(inner: Codec) -> Codec:
    """A nested archive: u32 size, bytes, then (UE5-era saves only) the UE4/UE5 object
    versions the buffer was written with. Buffers are not re-encoded when the engine is
    upgraded, so one save can mix legacy and current property tag formats.

    Decoded form: {'ue4_version', 'ue5_version', 'data'}; if the payload could not be
    decoded it is kept verbatim as {'raw': hex, 'error': str} instead of 'data'."""

    def read(r: Reader) -> dict[str, Any]:
        n = r.u32()
        buf = r.raw(n)
        if r.ue5:
            ue4 = r.u32()
            ue5 = r.u32()
            out: dict[str, Any] = {'ue4_version': ue4, 'ue5_version': ue5}
        else:
            ue5 = 0
            out = {}
        sub = r.sub(buf, ue5)
        try:
            out['data'] = inner[0](sub)
            if sub.p != n:
                raise ParseError('%d trailing bytes' % (n - sub.p))
        except (ParseError, struct.error, IndexError) as e:
            out.pop('data', None)
            out['error'] = str(e)[:300]
            out['raw'] = buf.hex()
        return out

    def write(w: Writer, v: dict[str, Any]) -> None:
        has_versions = 'ue5_version' in v
        if has_versions != bool(w.ue5):
            raise EncodeError('buffer versions must be present exactly when the save is UE5')
        if 'raw' in v:
            buf: bytes | bytearray = bytes.fromhex(v['raw'])
        else:
            sub = w.sub(v.get('ue5_version', 0))
            inner[1](sub, v['data'])
            buf = sub.b
        w.u32(len(buf))
        w.raw(buf)
        if has_versions:
            w.u32(v['ue4_version'])
            w.u32(v['ue5_version'])

    return read, write


NATIVE_STRUCTS = {
    'Vector': _reals(3),
    'Vector2D': _reals(2),
    'Vector4': _reals(4),
    'Quat': _reals(4),
    'Rotator': _reals(3),
    'Vector3f': _fixed(3, 'f', 4),
    'Vector2f': _fixed(2, 'f', 4),
    'IntPoint': _fixed(2, 'i', 4),
    'IntVector': _fixed(3, 'i', 4),
    'LinearColor': _fixed(4, 'f', 4),
    'Color': _fixed(4, 'B', 1),
    'Guid': _scalar('guid'),
    'DateTime': _scalar('i64'),
    'Timespan': _scalar('i64'),
    'SoftObjectPath': (_read_soft_path, _write_soft_path),
    'SoftClassPath': (_read_soft_path, _write_soft_path),
    # ES2
    'ESVariant': (_read_es_variant, _write_es_variant),
    'LocationStateBufferWrapper': _buffer_wrapper(_location_state),
    'DockableStationInstanceBufferWrapper': _buffer_wrapper(_station_instance),
}

# Legacy (pre-UE5.4) tags do not name the struct type of map keys/values or set
# elements, so it has to be known per property name: name -> (key struct, value struct)
LEGACY_STRUCT_HINTS = {
    'BufferedLocationSaveStates': (None, 'LocationStateBufferWrapper'),
    'BufferedDockableStationInstances': (None, 'DockableStationInstanceBufferWrapper'),
    'Attributes': (None, 'ESVariant'),
    'CustomData': (None, 'ESVariant'),
}


# ---------------------------------------------------------------- FText


def _read_format_arg(r: Reader) -> dict[str, Any]:
    t = r.u8()
    if t == 0:
        return {'int': r.i64()}
    if t == 1:
        return {'uint': r.u64()}
    if t == 2:
        return {'float': r.f32()}
    if t == 3:
        return {'double': r.f64()}
    if t == 4:
        return {'text': read_text(r)}
    if t == 5:
        return {'gender': r.u8()}
    raise ParseError('unknown format argument type %d at %#x' % (t, r.p))


_FORMAT_ARGS: list[tuple[str, Callable[[Writer, Any], None]]] = [
    ('int', Writer.i64),
    ('uint', Writer.u64),
    ('float', Writer.f32),
    ('double', Writer.f64),
    ('text', lambda w, v: write_text(w, v)),
    ('gender', Writer.u8),
]


def _write_format_arg(w: Writer, v: dict[str, Any]) -> None:
    for i, (key, wr) in enumerate(_FORMAT_ARGS):
        if key in v:
            w.u8(i)
            wr(w, v[key])
            return
    raise EncodeError('bad format argument %r' % (v,))


_NUMBER_OPTIONS = [
    ('always_sign', 'u32'),
    ('use_grouping', 'u32'),
    ('rounding_mode', 'i8'),
    ('min_integral', 'i32'),
    ('max_integral', 'i32'),
    ('min_fractional', 'i32'),
    ('max_fractional', 'i32'),
]


def read_text(r: Reader) -> dict[str, Any]:
    flags = r.u32()
    hist = r.i8()
    out: dict[str, Any] = {'flags': flags, 'history': hist}
    if hist == -1:  # None; optionally a culture-invariant string
        if r.u32():
            out['string'] = r.fstring()
    elif hist == 0:  # Base
        out['namespace'] = r.fstring()
        out['key'] = r.fstring()
        out['source'] = r.fstring()
    elif hist in (1, 3):  # NamedFormat, ArgumentFormat
        out['format'] = read_text(r)
        out['args'] = [[r.fstring(), _read_format_arg(r)] for _ in range(r.i32())]
    elif hist == 2:  # OrderedFormat
        out['format'] = read_text(r)
        out['args'] = [_read_format_arg(r) for _ in range(r.i32())]
    elif hist in (4, 5):  # AsNumber, AsPercent
        out['source_value'] = _read_format_arg(r)
        if r.u32():
            # FNumberFormattingOptions
            out['format_options'] = {k: getattr(r, f)() for k, f in _NUMBER_OPTIONS}
        out['culture'] = r.fstring()
    elif hist == 10:  # Transform
        out['source_text'] = read_text(r)
        out['transform'] = r.u8()
    elif hist == 11:  # StringTableEntry
        out['table'] = r.fstring()
        out['key'] = r.fstring()
    else:
        raise ParseError('unsupported FText history %d at %#x' % (hist, r.p))
    return out


def write_text(w: Writer, v: dict[str, Any]) -> None:
    hist = v['history']
    w.u32(v['flags'])
    w.i8(hist)
    if hist == -1:
        w.u32(1 if 'string' in v else 0)
        if 'string' in v:
            w.fstring(v['string'])
    elif hist == 0:
        w.fstring(v['namespace'])
        w.fstring(v['key'])
        w.fstring(v['source'])
    elif hist in (1, 3):
        write_text(w, v['format'])
        w.i32(len(v['args']))
        for k, a in v['args']:
            w.fstring(k)
            _write_format_arg(w, a)
    elif hist == 2:
        write_text(w, v['format'])
        w.i32(len(v['args']))
        for a in v['args']:
            _write_format_arg(w, a)
    elif hist in (4, 5):
        _write_format_arg(w, v['source_value'])
        w.u32(1 if 'format_options' in v else 0)
        if 'format_options' in v:
            for k, f in _NUMBER_OPTIONS:
                getattr(w, f)(v['format_options'][k])
        w.fstring(v['culture'])
    elif hist == 10:
        write_text(w, v['source_text'])
        w.u8(v['transform'])
    elif hist == 11:
        w.fstring(v['table'])
        w.fstring(v['key'])
    else:
        raise EncodeError('unsupported FText history %r' % (hist,))


def text_source(text: Any) -> str:
    """The readable part of an FText value from the save tree."""
    if not isinstance(text, dict):
        return ''
    if text.get('string'):
        return text['string']
    if text.get('source'):
        m = re.search(r'\|gender\w+\(([^,)]*)', text['source'])
        return m.group(1) if m else text['source']
    parts = [text_source(text.get('format'))]
    for a in text.get('args', []):
        a = a[1] if isinstance(a, list) else a
        if isinstance(a, dict) and 'text' in a:
            parts.append(text_source(a['text']))
    return ' '.join(x for x in parts if x)


# ---------------------------------------------------------------- property values

_SCALARS = {
    'IntProperty': 'i32',
    'UInt32Property': 'u32',
    'Int64Property': 'i64',
    'UInt64Property': 'u64',
    'Int16Property': 'i16',
    'UInt16Property': 'u16',
    'Int8Property': 'i8',
    'FloatProperty': 'f32',
    'DoubleProperty': 'f64',
    'StrProperty': 'fstring',
    'NameProperty': 'fstring',
    'ObjectProperty': 'fstring',
    'EnumProperty': 'fstring',
}
_DELEGATES = ('MulticastInlineDelegateProperty', 'MulticastDelegateProperty', 'MulticastSparseDelegateProperty')


def _looks_like_name(r: Reader) -> bool:
    n = struct.unpack_from('<i', r.d, r.p)[0] if r.p + 4 <= len(r.d) else 0
    if not 1 < n < 256 or r.p + 4 + n > len(r.d):
        return False
    s = r.d[r.p + 4 : r.p + 4 + n]
    return s[-1] == 0 and all(32 <= c < 127 for c in s[:-1])


def _is_variant(v: object) -> bool:
    return isinstance(v, dict) and 'variant' in v and not isinstance(v['variant'], dict)


def read_value(
    r: Reader, t: TypeName, in_container: bool = False, flags: int = 0, size: int | None = None, name: str | None = None
) -> tuple[Any, TypeName]:
    """Returns (value, type). The type is t, or a refined copy where the data itself
    revealed more than the tag did (legacy struct arrays)."""
    n = t.name
    f = _SCALARS.get(n)
    if f:
        return getattr(r, f)(), t
    if n == 'BoolProperty':
        return (bool(r.u8()) if in_container else bool(flags & F_BOOL_TRUE)), t
    if n == 'ByteProperty':
        if t.params:
            return (r.fstring() if t.params[0].name != 'None' else r.u8()), t
        if in_container and _is_legacy(r) and _looks_like_name(r):
            return r.fstring(), t  # legacy tags do not say whether a container holds enum bytes
        return r.u8(), t
    if n == 'SoftObjectProperty':
        return _read_soft_path(r), t
    if n == 'TextProperty':
        return read_text(r), t
    if n == 'StructProperty':
        sname = t.params[0].name if t.params else None
        if sname in NATIVE_STRUCTS:
            if t.hinted and _looks_like_name(r):
                return read_properties(r), t  # hint was wrong: a property name starts here
            return NATIVE_STRUCTS[sname][0](r), t
        return read_properties(r), t
    if n in ('ArrayProperty', 'SetProperty'):
        inner = t.params[0]
        if n == 'SetProperty' and r.i32() != 0:
            raise ParseError('SetProperty with removed elements at %#x' % r.p)
        cnt = r.i32()
        if cnt < 0:
            raise ParseError('negative count at %#x' % r.p)
        if inner.name == 'ByteProperty' and not inner.params:
            if size in (None, 4 + cnt) or n == 'SetProperty':
                return r.raw(cnt).hex(), t
            return [r.fstring() for _ in range(cnt)], t  # legacy enum array
        if n == 'ArrayProperty' and inner.name == 'StructProperty' and _is_legacy(r):
            inner = _read_legacy_array_struct_tag(r, name)
            t = TypeName(n, [inner])
        return [read_value(r, inner, in_container=True)[0] for _ in range(cnt)], t
    if n in _DELEGATES:
        return [{'object': r.fstring(), 'function': r.fstring()} for _ in range(r.i32())], t
    if n == 'DelegateProperty':
        return {'object': r.fstring(), 'function': r.fstring()}, t
    if n == 'MapProperty':
        kt, vt = t.params
        return _read_map(
            r, lambda r: read_value(r, kt, in_container=True)[0], lambda r: read_value(r, vt, in_container=True)[0]
        ), t
    raise ParseError('unknown property type %r at %#x' % (str(t)[:60], r.p))


def write_value(w: Writer, t: TypeName, v: Any, in_container: bool = False, name: str | None = None) -> None:
    n = t.name
    f = _SCALARS.get(n)
    if f:
        getattr(w, f)(v)
    elif n == 'BoolProperty':
        if in_container:
            w.u8(1 if v else 0)
    elif n == 'ByteProperty':
        if isinstance(v, str):
            w.fstring(v)
        else:
            w.u8(v)
    elif n == 'SoftObjectProperty':
        _write_soft_path(w, v)
    elif n == 'TextProperty':
        write_text(w, v)
    elif n == 'StructProperty':
        sname = t.params[0].name if t.params else None
        if sname in NATIVE_STRUCTS and not (sname == 'ESVariant' and not _is_variant(v)):
            NATIVE_STRUCTS[sname][1](w, v)
        else:
            write_properties(w, v)
    elif n in ('ArrayProperty', 'SetProperty'):
        inner = t.params[0]
        if n == 'SetProperty':
            w.i32(0)
        if isinstance(v, str):  # raw bytes
            if inner.name != 'ByteProperty':
                raise EncodeError('hex string given for an array of %s' % inner)
            data = bytes.fromhex(v)
            w.i32(len(data))
            w.raw(data)
            return
        if not isinstance(v, list):
            raise EncodeError('expected a list, got %s' % type(v).__name__)
        w.i32(len(v))
        patch = None
        if n == 'ArrayProperty' and inner.name == 'StructProperty' and _is_legacy(w):
            patch = _write_legacy_array_struct_tag(w, inner, name)
        start = len(w.b)
        for x in v:
            write_value(w, inner, x, in_container=True)
        if patch is not None:
            struct.pack_into('<q', w.b, patch, len(w.b) - start)
    elif n in _DELEGATES:
        w.i32(len(v))
        for d in v:
            w.fstring(d['object'])
            w.fstring(d['function'])
    elif n == 'DelegateProperty':
        w.fstring(v['object'])
        w.fstring(v['function'])
    elif n == 'MapProperty':
        kt, vt = t.params
        _write_map(
            w,
            v,
            lambda w, x: write_value(w, kt, x, in_container=True),
            lambda w, x: write_value(w, vt, x, in_container=True),
        )
    else:
        raise EncodeError('unknown property type %r' % str(t)[:60])


def _read_legacy_array_struct_tag(r: Reader, name: str | None) -> TypeName:
    """Legacy struct arrays carry one inner property tag describing the element struct."""
    at = r.p
    inner_name = r.fstring()
    if name is not None and inner_name != name:
        raise ParseError('struct array tag name %r != %r at %#x' % (inner_name, name, at))
    if r.fstring() != 'StructProperty':
        raise ParseError('bad struct array tag at %#x' % at)
    r.i64()  # total size of the elements, recomputed on write
    sname = r.fstring()
    if r.guid() != ZERO_GUID or r.u8():
        raise ParseError('struct array tag with a GUID at %#x' % at)
    return TypeName('StructProperty', [TypeName(sname)])


def _write_legacy_array_struct_tag(w: Writer, inner: TypeName, name: str | None) -> int:
    """Returns the offset of the i64 element size to patch."""
    if name is None or not inner.params:
        raise EncodeError('legacy struct array needs a property name and a struct type')
    w.fstring(name)
    w.fstring('StructProperty')
    patch = len(w.b)
    w.i64(0)
    w.fstring(inner.params[0].name)
    w.guid(ZERO_GUID)
    w.u8(0)
    return patch


def _read_legacy_tag(r: Reader, name: str, entry: Property) -> tuple[TypeName, int, int, int]:
    """FPropertyTag before UE5.4: type-specific fields follow the size."""
    tname = r.fstring()
    size = r.i32()
    idx = r.i32()
    params = []
    flags = 0
    hint = LEGACY_STRUCT_HINTS.get(name, (None, None))
    if tname == 'StructProperty':
        params = [TypeName(r.fstring())]
        g = r.guid()
        if g != ZERO_GUID:
            entry['struct_guid'] = g
    elif tname == 'BoolProperty':
        if r.u8():
            flags |= F_BOOL_TRUE
    elif tname in ('ByteProperty', 'EnumProperty'):
        params = [TypeName(r.fstring())]
    elif tname in ('ArrayProperty', 'SetProperty'):
        inner = TypeName(r.fstring())
        if inner.name == 'StructProperty' and tname == 'SetProperty' and hint[1]:
            inner.params = [TypeName(hint[1])]
            inner.hinted = True
        params = [inner]
    elif tname == 'MapProperty':
        params = [TypeName(r.fstring()), TypeName(r.fstring())]
        for p, h in zip(params, hint):
            if p.name == 'StructProperty' and h:
                p.params = [TypeName(h)]
                p.hinted = True
    if r.u8():
        entry['guid'] = r.guid()
    return TypeName(tname, params), size, flags, idx


def _write_legacy_tag(w: Writer, t: TypeName, idx: int, entry: Property) -> int:
    """Writes everything up to the value; returns the offset of the size field."""
    n = t.name
    w.fstring(n)
    patch = len(w.b)
    w.i32(0)
    w.i32(idx)
    if n == 'StructProperty':
        if not t.params:
            raise EncodeError('StructProperty without a struct name')
        w.fstring(t.params[0].name)
        w.guid(entry.get('struct_guid', ZERO_GUID))
    elif n == 'BoolProperty':
        w.u8(1 if entry['value'] else 0)
    elif n in ('ByteProperty', 'EnumProperty', 'ArrayProperty', 'SetProperty'):
        if not t.params:
            raise EncodeError('%s needs a type parameter in a legacy tag' % n)
        w.fstring(t.params[0].name)
    elif n == 'MapProperty':
        w.fstring(t.params[0].name)
        w.fstring(t.params[1].name)
    if 'guid' in entry:
        w.u8(1)
        w.guid(entry['guid'])
    else:
        w.u8(0)
    return patch


_INDEXED = re.compile(r'^(.*)\[(\d+)\]$')


def read_properties(r: Reader, unread: Collection[str] = ()) -> Properties:
    """Reads a tagged property list terminated by a 'None' name.

    Properties named in `unread` are not decoded: their bytes are kept under 'unread' and
    written back as they are, which saves most of the time on saves whose bulk is state that
    is not going to be looked at."""
    props: Properties = {}
    legacy = _is_legacy(r)
    while True:
        start = r.p
        name = r.fstring()
        if name == 'None':
            return props
        entry: Property = {'type': None, 'value': None}
        if legacy:
            t, size, flags, idx = _read_legacy_tag(r, name, entry)
        else:
            t = TypeName.read(r)
            size = r.i32()
            flags = r.u8()
            idx = r.i32() if flags & F_ARRAY_INDEX else 0
            if flags & F_GUID:
                entry['guid'] = r.guid()
            if flags & F_EXTENSIONS:
                raise ParseError('property extensions unsupported at %#x' % r.p)
            if flags & ~F_DERIVED:
                entry['flags'] = flags & ~F_DERIVED
        body = r.p
        if name in unread:
            if size < 0 or body + size > len(r.d):
                raise ParseError('bad size %d in %s@%#x' % (size, name, start))
            r.p = body + size
            entry = {'type': str(t), 'value': None, 'unread': bytes(r.d[start : r.p])}
            if name in props:
                raise ParseError('duplicate property %s@%#x' % (name, start))
            props[name] = entry
            continue
        try:
            entry['value'], t = read_value(r, t, flags=flags, size=size, name=name)
        except ParseError as e:
            raise ParseError('%s <- %s:%s@%#x' % (e, name, t, start)) from None
        except (struct.error, IndexError, ValueError) as e:
            raise ParseError('%r <- %s:%s@%#x' % (e, name, t, start)) from None
        if r.p != body + size:
            raise ParseError(
                'size mismatch in %s:%s@%#x: declared %d, consumed %d' % (name, t, start, size, r.p - body)
            )
        entry['type'] = str(t)
        key = name if not idx else '%s[%d]' % (name, idx)
        if key in props or key == 'None':
            raise ParseError('duplicate property %s@%#x' % (key, start))
        props[key] = entry


def write_properties(w: Writer, props: Properties) -> None:
    if not isinstance(props, dict):
        raise EncodeError('expected a property list, got %s' % type(props).__name__)
    legacy = _is_legacy(w)
    for key, entry in props.items():
        try:
            if not isinstance(entry, dict) or 'type' not in entry or 'value' not in entry:
                raise EncodeError('property needs "type" and "value"')
            if 'unread' in entry:  # kept as read, name and tag included
                w.raw(entry['unread'])
                continue
            m = _INDEXED.match(key)
            name, idx = (m.group(1), int(m.group(2))) if m else (key, 0)
            t = TypeName.parse(entry['type'])
            w.fstring(name)
            if legacy:
                patch = _write_legacy_tag(w, t, idx, entry)
            else:
                t.write(w)
                patch = len(w.b)
                w.i32(0)
                flags = entry.get('flags', 0) & ~F_DERIVED
                if idx:
                    flags |= F_ARRAY_INDEX
                if 'guid' in entry:
                    flags |= F_GUID
                if t.name == 'BoolProperty' and entry['value']:
                    flags |= F_BOOL_TRUE
                w.u8(flags)
                if idx:
                    w.i32(idx)
                if 'guid' in entry:
                    w.guid(entry['guid'])
            body = len(w.b)
            write_value(w, t, entry['value'], name=name)
            w.patch_i32(patch, len(w.b) - body)
        except EncodeError as e:
            raise EncodeError('%s <- %s' % (e, key)) from None
        except (KeyError, TypeError, ValueError) as e:
            raise EncodeError('%r <- %s' % (e, key)) from None
    w.fstring('None')


# ---------------------------------------------------------------- GVAS


def parse_gvas(data: bytes, unread: Collection[str] = ()) -> Save:
    r = Reader(data)
    if r.raw(4) != b'GVAS':
        raise ParseError('not GVAS')
    hdr: dict[str, Any] = {}
    hdr['save_game_version'] = r.u32()
    hdr['package_version_ue4'] = r.u32()
    if hdr['save_game_version'] >= 3:
        hdr['package_version_ue5'] = r.u32()
    r.ue5 = hdr.get('package_version_ue5', 0)
    hdr['engine_version'] = '%d.%d.%d' % (r.u16(), r.u16(), r.u16())
    hdr['engine_changelist'] = r.u32()
    hdr['engine_branch'] = r.fstring()
    hdr['custom_version_format'] = r.u32()
    hdr['custom_versions'] = [[r.guid(), r.u32()] for _ in range(r.u32())]
    hdr['save_game_class'] = r.fstring()
    if r.ue5 >= UE5_PROPERTY_TAG_EXTENSION:
        # EClassSerializationControlExtension, written before the root object's tagged properties
        hdr['serialization_control'] = r.u8()
        if hdr['serialization_control']:
            raise ParseError('unsupported serialization control extensions')
    props = read_properties(r, unread)
    hdr['trailer'] = data[r.p :].hex()
    return {'header': hdr, 'properties': props}


def build_gvas(save: Save) -> bytes:
    """Inverse of parse_gvas."""
    hdr = save['header']
    w = Writer(hdr.get('package_version_ue5', 0))
    w.raw(b'GVAS')
    w.u32(hdr['save_game_version'])
    w.u32(hdr['package_version_ue4'])
    if (hdr['save_game_version'] >= 3) != ('package_version_ue5' in hdr):
        raise EncodeError('package_version_ue5 must be present exactly when save_game_version >= 3')
    if 'package_version_ue5' in hdr:
        w.u32(hdr['package_version_ue5'])
    for part in hdr['engine_version'].split('.'):
        w.u16(int(part))
    w.u32(hdr['engine_changelist'])
    w.fstring(hdr['engine_branch'])
    w.u32(hdr['custom_version_format'])
    w.u32(len(hdr['custom_versions']))
    for g, v in hdr['custom_versions']:
        w.guid(g)
        w.u32(v)
    w.fstring(hdr['save_game_class'])
    if w.ue5 >= UE5_PROPERTY_TAG_EXTENSION:
        w.u8(hdr.get('serialization_control', 0))
    write_properties(w, save['properties'])
    w.raw(bytes.fromhex(hdr.get('trailer', '00000000')))
    return bytes(w.b)


def loads(data: bytes, unread: Collection[str] = ()) -> Save:
    """Decodes a save file's bytes. 'container' records whether it was compressed. The
    top-level properties named in `unread` are carried along undecoded (see read_properties);
    such a tree can be written back but not turned into JSON."""
    save = parse_gvas(unpack_container(data), unread)
    save['container'] = data[:4] != b'GVAS'
    return save


def dumps(save: Save, container: bool | None = None) -> bytes:
    """Encodes a tree from loads() back to file bytes."""
    gvas = build_gvas(save)
    if container is None:
        container = save.get('container', False)
    return pack_container(gvas) if container else gvas


def load(path: StrPath, unread: Collection[str] = ()) -> Save:
    with open(path, 'rb') as f:
        return loads(f.read(), unread)


def dump(save: Save, path: StrPath, container: bool | None = None) -> None:
    data = dumps(save, container)
    with open(path, 'wb') as f:
        f.write(data)
