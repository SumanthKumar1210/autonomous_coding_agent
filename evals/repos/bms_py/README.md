# bmstools

Offline analysis of battery management system (BMS) logs. Evaluation fixture
for AutoForge; it is frozen and only changes together with the eval cases.

## Log format

One sample per line, comma-separated:

```
time_ms,current_ma,cell1_mv,cell2_mv,...
```

`current_ma` is positive while charging. Every line has the same number of
cells.

## Modules

- `bmstools/log_parser.py` - parse log text into `Sample` objects
- `bmstools/stats.py` - statistics over a list of samples
- `bmstools/report.py` - human-readable summary of a log

## Conventions

- Python 3.11+, type hints everywhere, docstrings on public functions.
- Physical units are part of every name (`_mv`, `_ma`, `_ms`, `_wh`).
- Invalid input raises `LogFormatError` (parsing) or `ValueError` (statistics),
  never returns a sentinel.
- Every public function has a pytest test in `tests/`.
- `ruff check .` and `python -m pytest` must pass.
