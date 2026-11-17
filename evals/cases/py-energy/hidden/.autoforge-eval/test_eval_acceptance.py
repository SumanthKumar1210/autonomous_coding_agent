"""Hidden acceptance tests for eval case py-energy (never shown to the agent)."""

import pytest

from bmstools.log_parser import Sample
from bmstools.report import summary
from bmstools.stats import energy_wh

HOUR_MS = 3_600_000


def test_constant_power() -> None:
    samples = [Sample(0, 1000, (2000, 2000)), Sample(HOUR_MS, 1000, (2000, 2000))]
    assert energy_wh(samples) == pytest.approx(4.0)


def test_trapezoidal_rule() -> None:
    samples = [Sample(0, 0, (4000,)), Sample(HOUR_MS, 2000, (4000,))]
    assert energy_wh(samples) == pytest.approx(4.0)


def test_several_intervals() -> None:
    samples = [
        Sample(0, 1000, (4000,)),
        Sample(HOUR_MS // 2, 1000, (4000,)),
        Sample(HOUR_MS, 3000, (4000,)),
    ]
    assert energy_wh(samples) == pytest.approx(2.0 + 4.0)


def test_discharge_is_negative() -> None:
    samples = [Sample(0, -1000, (4000,)), Sample(HOUR_MS, -1000, (4000,))]
    assert energy_wh(samples) == pytest.approx(-4.0)


def test_fewer_than_two_samples() -> None:
    assert energy_wh([]) == 0.0
    assert energy_wh([Sample(0, 1000, (4000,))]) == 0.0


def test_timestamps_must_increase() -> None:
    with pytest.raises(ValueError):
        energy_wh([Sample(1000, 1, (4000,)), Sample(1000, 1, (4000,))])
    with pytest.raises(ValueError):
        energy_wh([Sample(1000, 1, (4000,)), Sample(500, 1, (4000,))])


def test_report_ends_with_energy() -> None:
    lines = summary([Sample(0, 1000, (3700, 3720)), Sample(2500, 3000, (3650, 3710))]).splitlines()
    assert lines[:5] == [
        "Samples: 2",
        "Duration: 2.5 s",
        "Cell voltage: 3650-3720 mV",
        "Max imbalance: 60 mV",
        "Average current: 2000 mA",
    ]
    assert lines[5:] == ["Energy: 0.01 Wh"]
    long_lines = summary([Sample(0, 1000, (2000, 2000)), Sample(HOUR_MS, 1250, (2000, 2000))])
    assert long_lines.splitlines()[-1] == "Energy: 4.50 Wh"
