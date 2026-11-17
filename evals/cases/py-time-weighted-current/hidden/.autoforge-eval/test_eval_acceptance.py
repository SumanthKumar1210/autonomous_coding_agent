"""Hidden acceptance tests for eval case py-time-weighted-current (never shown to the agent)."""

import pytest

from bmstools.log_parser import Sample
from bmstools.report import summary
from bmstools.stats import average_current_ma


def test_irregular_sampling_is_time_weighted() -> None:
    samples = [Sample(0, 1000, (3700,)), Sample(1000, 1000, (3700,)), Sample(1100, 3000, (3700,))]
    # 1000 mA for 1000 ms, then a 100 ms ramp to 3000 mA (mean 2000 mA): 1.2e6 mA*ms / 1100 ms
    assert average_current_ma(samples) == pytest.approx(1_200_000 / 1100)


def test_trapezoid_not_left_rectangles() -> None:
    samples = [Sample(0, 0, (3700,)), Sample(1000, 2000, (3700,))]
    assert average_current_ma(samples) == pytest.approx(1000.0)


def test_single_sample_returns_its_current() -> None:
    assert average_current_ma([Sample(5, 42, (3700,))]) == pytest.approx(42.0)


def test_invalid_input() -> None:
    with pytest.raises(ValueError):
        average_current_ma([])
    with pytest.raises(ValueError):
        average_current_ma([Sample(10, 1, (3700,)), Sample(10, 1, (3700,))])
    with pytest.raises(ValueError):
        average_current_ma([Sample(10, 1, (3700,)), Sample(5, 1, (3700,))])


def test_report_line_is_renamed() -> None:
    lines = summary([Sample(0, 1000, (3700, 3720)), Sample(2500, 3000, (3650, 3710))]).splitlines()
    assert lines == [
        "Samples: 2",
        "Duration: 2.5 s",
        "Cell voltage: 3650-3720 mV",
        "Max imbalance: 60 mV",
        "Average current (time-weighted): 2000 mA",
    ]
