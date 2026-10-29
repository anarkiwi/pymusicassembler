"""Assemble the native Music Assembler editor song (``S.`` file) from a Song.

The editor's order: player, patterns, orderlists (voice 3 first), arpeggio
programs, ``pattern_hi``/``pattern_lo`` (plus a zero entry), presets up to the
last non-zero byte; relocating moves the player's operands and pointers too.
"""

import struct
from typing import Dict, List, NamedTuple, Optional, Tuple

from pysidtracker import SidImage, require_ram
from pysidtracker.errors import reraise_as
from pysidtracker.relocate import relocate_operands, walk_code

from pymusicassembler import constants, grammar, reader
from pymusicassembler.errors import MusicAssemblerParseError, SongValidationError
from pymusicassembler.layout import Layout
from pymusicassembler.model import Song

_NO_ADDR = 0x10000

NATIVE_STUB = bytes.fromhex(
    "78204800a918a0008d14038c1503e88e0edce88e1ad05860ee19d02021004c31ea"
)


def native_stub(base: int) -> bytes:
    """The 33-byte self-start stub (:data:`NATIVE_STUB`) relocated to ``base``.

    ``SEI; JSR init; LDA #<irq; LDY #>irq; STA $0314; STY $0315; INX; STX $DC0E;
    INX; STX $D01A; CLI; RTS`` then the IRQ ``INC $D019; JSR play; JMP $EA31``.
    """
    stub = bytearray(NATIVE_STUB)
    stub[2:4] = struct.pack("<H", base + constants.NATIVE_INIT_OFFSET)
    irq = base + constants.NATIVE_IRQ_OFFSET
    stub[5], stub[7] = irq & 0xFF, irq >> 8
    stub[28:30] = struct.pack("<H", base + constants.NATIVE_PLAY_OFFSET)
    return bytes(stub)


def _source(song: Song) -> Tuple[SidImage, Layout, int]:
    """``(view, layout, base)`` of the source image; raises unless it is the editor player."""
    load = song.load_addr
    view = reader.load_view(song.image, load)
    base = reader.find_player_base(view, load, len(song.image))
    if base is None:
        raise SongValidationError(
            "player is not at the editor's layout (no play entry at base+$21, as "
            "in later or relocated-copy builds); cannot emit the S. format"
        )
    with reraise_as(SongValidationError, MusicAssemblerParseError):
        lay = reader.discover_layout(
            view, base + constants.NATIVE_PLAY_OFFSET, load, load + len(song.image)
        )
    commands = grammar.COMMANDS[grammar.orderlist_rule(lay)]
    if any(
        o.terminator == constants.ORDER_JUMP
        or any(e.pattern_id in commands for e in o.entries)
        for o in song.orderlists
    ) or any(
        p is not None and p.terminator == constants.PROGRAM_JUMP
        for p in song.inner_programs
    ):
        raise SongValidationError(
            "orderlist $FC/$FD or program $FD command (a later player build); "
            "the editor's S. player cannot represent it"
        )
    long = [
        i
        for i, p in enumerate(song.inner_programs)
        if p is not None and len(p.to_bytes()) > constants.PRESET_BANK_ARP_SLOT
    ]
    if long:
        raise SongValidationError(
            f"arpeggio programs {long} exceed the editor's "
            f"{constants.PRESET_BANK_ARP_SLOT}-byte program slot"
        )
    tables = (
        (lay.freq_lo, constants.FREQ_TABLE_LEN),
        (lay.freq_hi, constants.FREQ_TABLE_LEN),
        (lay.inner_lo, constants.ARP_COUNT),
        (lay.inner_hi, constants.ARP_COUNT),
    )
    if (
        lay.order_hi != lay.order_lo + constants.VOICES
        or load > base + constants.NATIVE_STUB_LEN
        or not all(
            base + constants.NATIVE_INIT_OFFSET < addr and addr + size <= lay.order_lo
            for addr, size in tables
        )
    ):
        raise SongValidationError(
            "player tables are not in the editor's order (code, note-frequency, "
            "arpeggio and orderlist pointers ahead of the song data)"
        )
    return view, lay, base


class _Item(NamedTuple):
    """A song-data blob: emit-order key, source extent, bytes and the ids it serves."""

    order: Tuple[int, int, int]
    src: int
    size: int
    blob: bytes
    ids: Tuple[int, ...] = ()


def _merge(items: List[_Item]) -> List[_Item]:
    """``items`` with ids at one source address holding equal bytes sharing one blob."""
    groups: Dict[tuple, _Item] = {}
    for item in items:
        key = (item.src, item.blob) if item.src < _NO_ADDR else (item.order,)
        old = groups.get(key)
        groups[key] = item if old is None else old._replace(ids=old.ids + item.ids)
    return list(groups.values())


def _place(items: List[_Item], view: SidImage, start: int, tmpl_end: int):
    """``(data, {item: address})``: ``items`` laid out in order from ``start``.

    While items follow their source order the source layout is kept: the
    editor's unused bytes between them, and an item overlapping the previous
    one (patterns may share a tail) stays inside it when its bytes still match.
    """
    prev = _Item((-1, 0, 0), tmpl_end, 0, b"")
    by_src = sorted((i for i in items if i.src < _NO_ADDR), key=lambda i: i.src)
    pred = dict(zip(by_src, [prev] + by_src))
    data = bytearray()
    at = {prev: start}
    anchor = reach = prev
    for item in sorted(items, key=lambda i: i.order):
        if pred.get(item) is prev:
            inner = at[anchor] - start + item.src - anchor.src
            if item.src < reach.src + reach.size and (
                data[inner : inner + len(item.blob)] == item.blob
            ):
                at[item] = start + inner
                reach = max(reach, item, key=lambda i: i.src + i.size)
                prev = item
                continue
            gap = range(reach.src + reach.size, item.src)
            data += bytes(view.peek(a) for a in gap)
        at[item] = start + len(data)
        data += item.blob
        anchor = reach = prev = item
    return data, at


def _items(song: Song, view: SidImage, lay: Layout, region: range) -> List[_Item]:
    """The song's patterns, orderlists (voice 3 first) and programs, with source extents."""

    def source(lo, hi, idx):
        addr = view.ptr(lo, hi, idx)
        return addr if addr in region else _NO_ADDR

    items = []
    ntab = abs(lay.pattern_lo - lay.pattern_hi)
    for pid, pat in enumerate(song.patterns):
        if pat is None:  # the player never reads it: no data to place
            continue
        addr = source(lay.pattern_lo, lay.pattern_hi, pid) if pid < ntab else _NO_ADDR
        size = (
            len(reader.parse_pattern(view, lay, pid).to_bytes())
            if addr < _NO_ADDR
            else 0
        )
        items.append(_Item((0, addr, pid), addr, size, pat.to_bytes(), (pid,)))
    rule = grammar.orderlist_rule(lay)
    for voice, orderlist in enumerate(song.orderlists):
        addr = source(lay.order_lo, lay.order_hi, voice)
        size = len(reader.parse_orderlist(view, lay, voice, rule).to_bytes())
        items.append(_Item((1, -voice, 0), addr, size, orderlist.to_bytes(), (voice,)))
    for i, prog in enumerate(song.inner_programs):
        if prog is None:
            continue
        old = reader.stored_program(view, lay, i, region)
        addr = view.ptr(lay.inner_lo, lay.inner_hi, i) if old else _NO_ADDR
        size = len(old.to_bytes()) if old else 0
        items.append(_Item((2, addr, i), addr, size, prog.to_bytes(), (i,)))
    return _merge(items)


def assemble(song: Song, base: Optional[int] = None) -> Tuple[int, bytes]:
    """``(base, body)``: the editor-layout song at ``base`` (default the source's).

    ``body`` has no load-address prefix: the self-start stub, the player and
    the song data. Raises :class:`SongValidationError` for a non-editor build.
    """
    # pylint: disable=too-many-locals  # one variable per table
    view, lay, src = _source(song)
    grammar.check(song, lay)
    src_end = song.load_addr + len(song.image)
    base = src if base is None else base
    if not constants.NATIVE_BASE_MIN <= base <= constants.NATIVE_BASE_MAX:
        raise SongValidationError(
            f"native base {base:#06x} outside the editor's "
            f"{constants.NATIVE_BASE_MIN:#06x}-{constants.NATIVE_BASE_MAX:#06x}"
        )
    code = walk_code(
        view, (lay.entry, lay.init), src + constants.NATIVE_STUB_LEN, src_end
    )
    tmpl_end = lay.order_hi + constants.VOICES
    while tmpl_end in code:  # player code placed after the orderlist pointers
        tmpl_end += code[tmpl_end]
    region = range(tmpl_end, src_end)
    tmpl = bytearray(native_stub(base))
    tmpl += bytes(view.peek(a) for a in range(src + len(tmpl), tmpl_end))

    def put(addr, values):
        tmpl[addr - src : addr - src + len(values)] = bytes(v & 0xFF for v in values)

    def put_ptrs(lo, hi, index, addrs):
        put(lo + index, addrs)
        put(hi + index, [a >> 8 for a in addrs])

    put(lay.freq_lo, song.freq_lo)
    put(lay.freq_hi, song.freq_hi)

    items = _items(song, view, lay, region)
    npat = len(song.patterns) + 1
    ntab = abs(lay.pattern_lo - lay.pattern_hi)
    instr_blob = b"".join(i.to_bytes() for i in song.instruments)
    if lay.instr in region and lay.instr == max(lay.tables().values()):
        kept = max(len(instr_blob.rstrip(b"\0")), region.stop - lay.instr)
        instr_blob = instr_blob[:kept]  # the source's partial last record
    tables = [
        _Item((3, 0, 0), lay.pattern_hi, ntab, bytes(npat)),
        _Item((4, 0, 0), lay.pattern_lo, ntab, bytes(npat)),
        _Item((5, 0, 0), lay.instr, len(instr_blob), instr_blob),
    ]
    tables = [t if t.src in region else t._replace(src=_NO_ADDR) for t in tables]
    start = base + len(tmpl)
    data, at = _place(items + tables, view, start, tmpl_end)
    pattern_hi, pattern_lo, instr = (at[t] for t in tables)
    found: Tuple[Dict[int, int], ...] = ({}, {}, {})
    for item in items:
        found[item.order[0]].update(dict.fromkeys(item.ids, at[item]))
    pattern_at, order_at, program_at = found
    ptrs = [
        pattern_at.get(pid, view.ptr(lay.pattern_lo, lay.pattern_hi, pid))
        for pid in range(len(song.patterns))
    ] + [0]
    data[pattern_hi - start : pattern_hi - start + npat] = bytes(a >> 8 for a in ptrs)
    data[pattern_lo - start : pattern_lo - start + npat] = bytes(a & 0xFF for a in ptrs)

    # The editor relocates inner_hi entries 1-16 (one past the table) by page.
    for addr in range(lay.inner_hi + 1, lay.inner_hi + constants.ARP_COUNT + 1):
        if addr < lay.order_lo and not 0 <= addr - lay.inner_lo < constants.ARP_COUNT:
            put(addr, (view.peek(addr) + (base >> 8) - (src >> 8),))
    for i in range(constants.ARP_COUNT):
        if i in program_at or reader.stored_program(view, lay, i, region):
            # 0: a slot the song emptied
            put_ptrs(lay.inner_lo, lay.inner_hi, i, [program_at.get(i, 0)])
    put_ptrs(
        lay.order_lo, lay.order_hi, 0, [order_at[v] for v in range(constants.VOICES)]
    )

    remap = {lay.pattern_lo: pattern_lo, lay.pattern_hi: pattern_hi}
    remap.update((lay.instr + k, instr + k) for k in range(constants.INSTR_RECORD_SIZE))
    moves = relocate_operands(
        view, code, range(src, tmpl_end), base - src, remap, region, SongValidationError
    )
    for operand, target in moves.items():
        put(operand, struct.pack("<H", target))

    body = bytes(tmpl + data)
    if base + len(body) > 0x10000:
        raise SongValidationError(
            f"native song [{base:#06x},{base + len(body):#06x}) runs past $FFFF"
        )
    require_ram(base, len(body), "native song", SongValidationError)
    return base, body
