"""Hidden acceptance tests for eval case py-parser-robust (never shown to the agent)."""

import pytest

from bmstools.log_parser import LogFormatError, parse_line, parse_log

LOG = """# logger v2
time_ms,current_ma,cell1_mv,cell2_mv

0,100,3700,3701
   # mid comment
1000,100,3702,3703
"""


def test_skips_header_comments_and_blank_lines() -> None:
    samples = parse_log(LOG)
    assert [s.time_ms for s in samples] == [0, 1000]
    assert samples[1].cell_mv == (3702, 3703)


def test_whitespace_only_lines_are_skipped() -> None:
    assert len(parse_log("0,1,3700\n   \n\t\n1,1,3700\n")) == 2


def test_malformed_line_reports_line_number() -> None:
    with pytest.raises(LogFormatError) as info:
        parse_log("0,100,3700\n\n1000,x,3700\n")
    assert info.value.line_no == 3


def test_short_line_reports_line_number() -> None:
    with pytest.raises(LogFormatError) as info:
        parse_log("# c\n0,100,3700\n1000,100\n")
    assert info.value.line_no == 3


def test_inconsistent_cell_count_is_rejected() -> None:
    with pytest.raises(LogFormatError) as info:
        parse_log("0,100,3700,3701\n1000,100,3700\n")
    assert info.value.line_no == 2


def test_header_is_only_allowed_first() -> None:
    with pytest.raises(LogFormatError) as info:
        parse_log("0,100,3700\ntime_ms,current_ma,cell1_mv\n")
    assert info.value.line_no == 2


def test_empty_logs() -> None:
    assert parse_log("") == []
    assert parse_log("# nothing\n\n") == []
    assert parse_log("time_ms,current_ma,cell1_mv\n") == []


def test_parse_line_unchanged() -> None:
    assert parse_line("5, -10, 3700").cell_mv == (3700,)
