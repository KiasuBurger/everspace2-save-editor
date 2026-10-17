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

"""The search of every catalyst seed for the one that is best for an item.

The best seed is the one whose worst factor among the scored attributes is highest, ties
broken by the mean.
"""

import os
import signal
import threading
from collections.abc import Callable, Iterable

from .analysis import LCG_ADD, LCG_MUL, UNSET_SEEDS, Kind, UsageError, effective, factors, to_int32

SEARCH_CHUNK = 1 << 17  # seeds scored at a time; its buffers (3 MB) fit in a core's cache
SEARCH_SPAN = 32  # chunks per unit of work handed to a worker process
_local = threading.local()


class SearchCancelled(Exception):
    """Raised by search() when the event it was given to watch is set."""


def default_jobs() -> int:
    """Worker processes to use for the search: half the available CPUs, which is the number
    of cores on a machine with two threads per core; the second thread of a core only slows
    the search down."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:  # not available on every platform
        cpus = os.cpu_count() or 1
    return max(1, cpus // 2)


def _ignore_interrupt() -> None:
    """Leaves Ctrl-C to the process running the search, which stops its workers."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _score_span(plan: list[tuple[bool, bool]], count: int, top: int, first: int, last: int) -> list[tuple[int, int]]:
    """[(key, seed)] for the best `top` seeds in chunks first..last-1; a larger key is better.
    plan is [(scored, inverted)] for each draw and count the number of scored draws.
    Works entirely in buffers kept between calls: allocating fresh arrays for every chunk
    costs more than scoring it."""
    import numpy as np

    try:
        offsets, s, good, worst, total, key = _local.buffers
    except AttributeError:
        _local.buffers = (
            np.arange(SEARCH_CHUNK, dtype=np.uint32),
            np.empty(SEARCH_CHUNK, np.uint32),
            np.empty(SEARCH_CHUNK, np.uint32),
            np.empty(SEARCH_CHUNK, np.uint32),
            np.empty(SEARCH_CHUNK, np.uint64),
            np.empty(SEARCH_CHUNK, np.uint64),
        )
        offsets, s, good, worst, total, key = _local.buffers
    mul, add, shift = np.uint32(LCG_MUL), np.uint32(LCG_ADD), np.uint32(9)
    full = np.uint32((1 << 23) - 1)
    divisor = np.uint64(count)
    found = []
    for c in range(first, last):
        base = c * SEARCH_CHUNK
        np.add(offsets, np.uint32(base), out=s)
        worst.fill(0xFFFFFFFF)
        total.fill(0)
        for use, invert in plan:
            np.multiply(s, mul, out=s)
            np.add(s, add, out=s)
            if use:
                np.right_shift(s, shift, out=good)  # the 23-bit fraction
                if invert:
                    np.subtract(full, good, out=good)
                np.minimum(worst, good, out=worst)
                np.add(total, good, out=total)
        # worst factor in the high half, mean in the low half
        np.copyto(key, worst)
        np.left_shift(key, np.uint64(32), out=key)
        np.floor_divide(total, divisor, out=total)
        np.add(key, total, out=key)
        for unset in UNSET_SEEDS:  # values the game uses for "no seed"
            if base <= unset < base + SEARCH_CHUNK:
                key[unset - base] = 0
        for _ in range(min(top, SEARCH_CHUNK)):
            j = int(key.argmax())
            found.append((int(key[j]), base + j))
            key[j] = 0
    return sorted(found, reverse=True)[:top]


def search(
    kinds: Iterable[Kind],
    top: int = 3,
    include_negative: bool = False,
    progress: Callable[[float], object] | None = None,
    jobs: int | None = None,
    cancel: threading.Event | None = None,
) -> list[tuple[int, float, float]]:
    """The `top` catalyst seeds with the best worst-case factor over the scored attributes.
    Returns [(seed as int32, worst effective factor, mean effective factor)].

    The seed space is cut into spans that are scored independently by `jobs` worker
    processes (see default_jobs), or here when jobs is 1. Processes rather than threads
    because numpy only releases the GIL inside each array operation, which stops threads
    scaling past a few; a script calling this needs the usual `if __name__ == '__main__':`
    guard, since the workers import it. The result does not depend on the number of workers.

    Setting `cancel` stops the search, which then raises SearchCancelled, once the work in
    hand is done: the spans not yet started are dropped, so the worker processes go too."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise UsageError('the seed search needs numpy; run it through "uv run" in this project') from None

    scored = {i: d for i, _, d in kinds if d}
    if not scored:
        raise UsageError('no attributes left to score')
    jobs = default_jobs() if jobs is None else jobs
    if jobs < 1:
        raise UsageError('jobs must be at least 1')
    draws = max(scored) + 1
    limit = 1 << 32 if include_negative else 1 << 31  # the game's own seeds are all positive
    chunks = limit // SEARCH_CHUNK
    plan = [(i in scored, scored.get(i, 0) < 0) for i in range(draws)]
    spans = [(plan, len(scored), top, c, min(c + SEARCH_SPAN, chunks)) for c in range(0, chunks, SEARCH_SPAN)]

    best: list[tuple[int, int]] = []

    def collect(done: int, found: list[tuple[int, int]]) -> None:
        nonlocal best
        if cancel and cancel.is_set():
            raise SearchCancelled()
        best.extend(found)
        if len(best) > 64 * top:
            best = sorted(best, reverse=True)[:top]
        if progress:
            progress(done / len(spans))

    if jobs == 1:
        for done, span in enumerate(spans, 1):
            collect(done, _score_span(*span))
    else:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        # never a plain fork: the editor searches from a thread of a process running GTK
        method = 'forkserver' if 'forkserver' in multiprocessing.get_all_start_methods() else 'spawn'
        with ProcessPoolExecutor(
            max_workers=min(jobs, len(spans)),
            mp_context=multiprocessing.get_context(method),
            initializer=_ignore_interrupt,
        ) as pool:
            pending = [pool.submit(_score_span, *span) for span in spans]
            try:
                for done, future in enumerate(as_completed(pending), 1):
                    collect(done, future.result())
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
    # ties on the key are broken by the larger seed, so the order of completion does not matter
    best = sorted(best, reverse=True)[:top]
    out = []
    for _, seed in best:
        fac = factors(seed, draws)
        eff = [effective(fac[i], d) for i, d in scored.items()]
        out.append((to_int32(seed), min(eff), sum(eff) / len(eff)))
    return out
