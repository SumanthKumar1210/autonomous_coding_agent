import pytest

from bmstools.log_parser import LogFormatError, Sample, parse_line, parse_log


def test_parse_line() -> None:
    sample = parse_line("1000, -2500, 3700,3710,3695")
    assert sample == Sample(time_ms=1000, current_ma=-2500, cell_mv=(3700, 3710, 3695))
    assert sample.pack_mv == 11105


def test_parse_line_rejects_short_and_non_numeric_lines() -> None:
    with pytest.raises(LogFormatError):
        parse_line("1000,200")
    with pytest.raises(LogFormatError):
        parse_line("1000,abc,3700")


def test_parse_log() -> None:
    samples = parse_log("0,100,3700,3700\n1000,100,3701,3702\n")
    assert [s.time_ms for s in samples] == [0, 1000]
