"""Human-readable summary of a BMS log."""

from __future__ import annotations

from collections.abc import Sequence

from bmstools.log_parser import Sample
from bmstools.stats import average_current_ma, cell_extremes_mv, max_imbalance_mv


def summary(samples: Sequence[Sample]) -> str:
    """Multi-line text summary of a log: duration, cell range, imbalance, current.

    Raises:
        ValueError: If ``samples`` is empty.
    """
    min_mv, max_mv = cell_extremes_mv(samples)
    duration_s = (samples[-1].time_ms - samples[0].time_ms) / 1000
    lines = [
        f"Samples: {len(samples)}",
        f"Duration: {duration_s:.1f} s",
        f"Cell voltage: {min_mv}-{max_mv} mV",
        f"Max imbalance: {max_imbalance_mv(samples)} mV",
        f"Average current: {average_current_ma(samples):.0f} mA",
    ]
    return "\n".join(lines)
