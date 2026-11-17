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


def test_parse_log_skips_header_comments_and_blank_lines() -> None:
    text = "# logger\ntime_ms,current_ma,cell1_mv\n\n0,1,3700\n  # note\n1,1,3701\n"
    assert [s.time_ms for s in parse_log(text)] == [0, 1]


def test_parse_log_reports_line_number() -> None:
    with pytest.raises(LogFormatError) as info:
        parse_log("0,1,3700\n\n1,x,3700\n")
    assert info.value.line_no == 3


def test_parse_log_rejects_changing_cell_count() -> None:
    with pytest.raises(LogFormatError) as info:
        parse_log("0,1,3700,3700\n1,1,3700\n")
    assert info.value.line_no == 2
