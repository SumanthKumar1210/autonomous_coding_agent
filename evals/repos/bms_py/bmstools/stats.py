"""Statistics over a list of samples."""

from __future__ import annotations

from collections.abc import Sequence

from bmstools.log_parser import Sample


def _require(samples: Sequence[Sample]) -> None:
    if not samples:
        raise ValueError("at least one sample is required")


def cell_extremes_mv(samples: Sequence[Sample]) -> tuple[int, int]:
    """Lowest and highest cell voltage over all samples, as ``(min_mv, max_mv)``.

    Raises:
        ValueError: If ``samples`` is empty.
    """
    _require(samples)
    return (min(min(s.cell_mv) for s in samples), max(max(s.cell_mv) for s in samples))


def max_imbalance_mv(samples: Sequence[Sample]) -> int:
    """Largest difference between the highest and lowest cell within one sample (mV).

    Raises:
        ValueError: If ``samples`` is empty.
    """
    _require(samples)
    return max(max(s.cell_mv) - min(s.cell_mv) for s in samples)


def average_current_ma(samples: Sequence[Sample]) -> float:
    """Arithmetic mean of the pack current over all samples (mA).

    Raises:
        ValueError: If ``samples`` is empty.
    """
    _require(samples)
    return sum(s.current_ma for s in samples) / len(samples)
