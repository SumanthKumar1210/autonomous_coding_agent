"""Parse BMS CSV logs into samples.

Each line is ``time_ms,current_ma,cell1_mv,cell2_mv,...``.
"""

from __future__ import annotations

from dataclasses import dataclass


class LogFormatError(ValueError):
    """A log line could not be parsed.

    Attributes:
        line_no: 1-based line number in the log, or ``None`` if unknown.
    """

    def __init__(self, message: str, line_no: int | None = None) -> None:
        self.line_no = line_no
        where = f"line {line_no}: " if line_no is not None else ""
        super().__init__(f"{where}{message}")


@dataclass(frozen=True)
class Sample:
    """One measurement of the pack.

    Attributes:
        time_ms: Milliseconds since the start of logging.
        current_ma: Pack current in mA, positive while charging.
        cell_mv: Voltage of every cell in mV.
    """

    time_ms: int
    current_ma: int
    cell_mv: tuple[int, ...]

    @property
    def pack_mv(self) -> int:
        """Pack voltage: the sum of all cell voltages (mV)."""
        return sum(self.cell_mv)


def parse_line(line: str) -> Sample:
    """Parse one log line.

    Raises:
        LogFormatError: If the line has fewer than three fields or a field is not an integer.
    """
    fields = [f.strip() for f in line.strip().split(",")]
    if len(fields) < 3:
        raise LogFormatError(
            f"expected time, current and at least one cell, got {len(fields)} field(s)"
        )
    try:
        values = [int(f) for f in fields]
    except ValueError as exc:
        raise LogFormatError(f"not an integer: {exc}") from exc
    return Sample(time_ms=values[0], current_ma=values[1], cell_mv=tuple(values[2:]))


def parse_log(text: str) -> list[Sample]:
    """Parse a whole log, one sample per line.

    Raises:
        LogFormatError: If any line cannot be parsed.
    """
    return [parse_line(line) for line in text.splitlines()]
