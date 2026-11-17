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
    """Time-weighted mean of the pack current (mA), by the trapezoidal rule.

    A single sample returns its own current.

    Raises:
        ValueError: If ``samples`` is empty or the timestamps do not strictly increase.
    """
    _require(samples)
    if len(samples) == 1:
        return float(samples[0].current_ma)
    charge_ma_ms = 0.0
    for prev, cur in zip(samples, samples[1:], strict=False):
        dt_ms = cur.time_ms - prev.time_ms
        if dt_ms <= 0:
            raise ValueError(
                f"timestamps must strictly increase: {prev.time_ms} then {cur.time_ms}"
            )
        charge_ma_ms += (prev.current_ma + cur.current_ma) / 2 * dt_ms
    return charge_ma_ms / (samples[-1].time_ms - samples[0].time_ms)
