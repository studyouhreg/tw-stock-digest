"""Pluggable stock-selection strategies (strategy pattern).

>>> from tw_stock_digest import strategies
>>> picks = strategies.get('high_52w').select(view)
"""
from .base import REGISTRY, MarketView, Pick, Strategy, get, register
from . import catalog  # noqa: F401  (registers the built-in strategies)
from .catalog import Screen

__all__ = ['REGISTRY', 'MarketView', 'Pick', 'Screen', 'Strategy', 'get', 'register']
