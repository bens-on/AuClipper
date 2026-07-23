"""Export trend signal collector public API."""

from modules.trend_signal.collector import collect_signals
from modules.trend_signal.errors import CollectorError, ConfigurationError

__all__ = [
    "CollectorError",
    "ConfigurationError",
    "collect_signals",
]
