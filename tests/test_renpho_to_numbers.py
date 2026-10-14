"""Renpho export → Numbers sheet rows."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "renpho_to_numbers.py"
_spec = importlib.util.spec_from_file_location("renpho_to_numbers", _SCRIPT)
assert _spec is not None and _spec.loader is not None
renpho = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renpho)


_HEADER = "Date,Time,Weight(lb),Body Fat Percentage(%)"


def _line(day: str, clock: str, pounds: str, fat: str) -> str:
    return f"{day},{clock},{pounds},{fat}"


def test_fills_missing_days_and_keeps_the_earlier_reading(tmp_path: Path):
    csv_path = tmp_path / "renpho.csv"
    csv_path.write_text(
        "\n".join(
            [
                _HEADER,
                _line("2026.07.20", "07:57:26", "164.4", "21.8"),
                _line("2026.07.20", "07:56:59", "164.0", "21.7"),
                _line("2026.07.18", "06:47:30", "159.2", "20.5"),
                _line("2026.07.12", "09:14:48", "164.0", "--"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    readings = renpho.readings_from_renpho(csv_path)
    by_day = {row["metric_date"]: row for row in readings}
    assert by_day[date(2026, 7, 20)]["body_weight_lbs"] == 164.0
    assert by_day[date(2026, 7, 20)]["body_fat_pct"] == 21.7
    assert by_day[date(2026, 7, 12)]["body_fat_pct"] is None

    sheet = renpho.numbers_rows(readings)
    labels = [row["Day"] for row in sheet]
    assert labels[0] == "July 12, 2026"
    assert labels[-1] == "July 20, 2026"
    assert "July 13, 2026" in labels
    gap = next(row for row in sheet if row["Day"] == "July 13, 2026")
    assert gap["Weight (lbs)"] == ""
    assert gap["Body Fat(%)"] == ""
    july_12 = sheet[0]
    assert july_12["Weight (lbs)"] == "164.0"
    assert july_12["Body Fat(%)"] == ""
