from bmstools.log_parser import Sample
from bmstools.report import summary


def test_summary() -> None:
    text = summary([Sample(0, 1000, (3700, 3720)), Sample(2500, 3000, (3650, 3710))])
    assert text.splitlines() == [
        "Samples: 2",
        "Duration: 2.5 s",
        "Cell voltage: 3650-3720 mV",
        "Max imbalance: 60 mV",
        "Average current: 2000 mA",
    ]
