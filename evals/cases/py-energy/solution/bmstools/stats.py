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


def energy_wh(samples: Sequence[Sample]) -> float:
    """Energy into the pack in Wh (negative while discharging), by the trapezoidal rule.

    Integrates pack power (pack voltage times current); fewer than two samples give ``0.0``.

    Raises:
        ValueError: If the timestamps do not strictly increase.
    """
    total_uw_ms = 0.0  # mV x mA = uW, times ms
    for prev, cur in zip(samples, samples[1:], strict=False):
        dt_ms = cur.time_ms - prev.time_ms
        if dt_ms <= 0:
            raise ValueError(
                f"timestamps must strictly increase: {prev.time_ms} then {cur.time_ms}"
            )
        total_uw_ms += (prev.pack_mv * prev.current_ma + cur.pack_mv * cur.current_ma) / 2 * dt_ms
    return total_uw_ms / 1e6 / 3_600_000
