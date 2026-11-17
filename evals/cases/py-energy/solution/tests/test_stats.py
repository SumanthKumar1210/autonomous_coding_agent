import pytest

from bmstools.log_parser import Sample
from bmstools.stats import average_current_ma, cell_extremes_mv, energy_wh, max_imbalance_mv

SAMPLES = [
    Sample(0, 1000, (3700, 3720)),
    Sample(1000, 3000, (3650, 3710)),
]


def test_cell_extremes() -> None:
    assert cell_extremes_mv(SAMPLES) == (3650, 3720)


def test_max_imbalance() -> None:
    assert max_imbalance_mv(SAMPLES) == 60


def test_average_current() -> None:
    assert average_current_ma(SAMPLES) == 2000


def test_empty_input_is_rejected() -> None:
    for fn in (cell_extremes_mv, max_imbalance_mv, average_current_ma):
        with pytest.raises(ValueError):
            fn([])


def test_energy_wh() -> None:
    hour_ms = 3_600_000
    assert energy_wh([Sample(0, 0, (4000,)), Sample(hour_ms, 2000, (4000,))]) == pytest.approx(4.0)
    discharge = [Sample(0, -1000, (4000,)), Sample(hour_ms, -1000, (4000,))]
    assert energy_wh(discharge) == pytest.approx(-4.0)
    assert energy_wh(SAMPLES[:1]) == 0.0


def test_energy_wh_rejects_non_increasing_time() -> None:
    with pytest.raises(ValueError):
        energy_wh([Sample(5, 1, (4000,)), Sample(5, 1, (4000,))])
