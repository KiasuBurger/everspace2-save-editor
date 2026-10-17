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

"""Chaos catalyst outcomes for the items in a save: predicting them and searching for the best.

analysis     what the game does with a catalyst seed, and with an item's attributes
seed_search  the search of every seed for the best one
"""

from .analysis import (
    ALWAYS_ALLOWED,
    CATALYST_GRANTS,
    FACTOR_MIN,
    FACTOR_SPAN,
    LCG_ADD,
    LCG_MUL,
    LOWER_IS_BETTER,
    MINING_RANGES_FROM,
    NOT_STATS,
    OPTIMIZED,
    OPTIMIZED_CATALYST,
    UNSET_SEEDS,
    Analysis,
    Attribute,
    Kind,
    UsageError,
    analyse,
    catalyst_attributes,
    classify,
    effective,
    factors,
    fractions,
    maximised,
    to_int32,
)
from .seed_search import SEARCH_CHUNK, SEARCH_SPAN, SearchCancelled, default_jobs, search

__all__ = [
    'ALWAYS_ALLOWED',
    'CATALYST_GRANTS',
    'FACTOR_MIN',
    'FACTOR_SPAN',
    'LCG_ADD',
    'LCG_MUL',
    'LOWER_IS_BETTER',
    'MINING_RANGES_FROM',
    'NOT_STATS',
    'OPTIMIZED',
    'OPTIMIZED_CATALYST',
    'SEARCH_CHUNK',
    'SEARCH_SPAN',
    'UNSET_SEEDS',
    'Analysis',
    'Attribute',
    'Kind',
    'SearchCancelled',
    'UsageError',
    'analyse',
    'catalyst_attributes',
    'classify',
    'default_jobs',
    'effective',
    'factors',
    'fractions',
    'maximised',
    'search',
    'to_int32',
]
