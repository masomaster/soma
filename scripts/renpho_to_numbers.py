"""Turn a Renpho Health CSV into a day-by-day sheet for Numbers.

Reads the export (newest day first, skipped days, extra body-comp columns)
and writes one row per calendar day from the first weigh-in through the last:

    Day, Weight (lbs), 7-day Prior avg., Weekly weight gain/loss, Body Fat(%), Notes

The 7-day prior average is the mean of weigh-ins in the 7 calendar days ending
that day. Weekly weight gain/loss is that average minus the average from 7 days
earlier. Missing days are blank in Weight and Body Fat, and still get an
average when any weigh-in falls in the window.

Two readings on the same day keep the earlier time. ``--`` body fat is blank.

    python scripts/renpho_to_numbers.py ~/Downloads/RENPHO\\ Health-Mason.csv
    python scripts/renpho_to_numbers.py in.csv -o ~/Downloads/RENPHO-for-Numbers.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.weight_tracking import daily_weight_log

_COLUMNS = (
    "Day",
    "Weight (lbs)",
    "7-day Prior avg.",
    "Weekly weight gain/loss",
    "Body Fat(%)",
    "Notes",
)


def _missing(value: str | None) -> bool:
    if value is None:
        return True
    text = value.strip()
    return not text or text == "--"


def _parse_day(value: str) -> date:
    text = value.strip()
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date {value!r}")


def readings_from_renpho(path: Path) -> list[dict[str, Any]]:
    """One reading per day. The earliest time that day wins."""
    by_day: dict[date, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "Date" not in reader.fieldnames:
            raise ValueError(f"{path} is not a Renpho Health CSV (missing Date column)")
        for raw in reader:
            if _missing(raw.get("Date")) or _missing(raw.get("Weight(lb)")):
                continue
            day = _parse_day(raw["Date"])
            clock = (raw.get("Time") or "").strip() or "00:00:00"
            fat = None if _missing(raw.get("Body Fat Percentage(%)")) else float(raw["Body Fat Percentage(%)"])
            row = {
                "metric_date": day,
                "time": clock,
                "body_weight_lbs": float(raw["Weight(lb)"]),
                "body_fat_pct": fat,
            }
            current = by_day.get(day)
            if current is None or clock < current["time"]:
                by_day[day] = row
    return list(by_day.values())


def _cell(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.1f}"


def _day_label(day: date) -> str:
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def numbers_rows(readings: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Sheet rows, oldest day first, with gaps filled."""
    log = daily_weight_log(readings)
    out: list[dict[str, str]] = []
    for row in log:
        out.append(
            {
                "Day": _day_label(row["metric_date"]),
                "Weight (lbs)": _cell(row["body_weight_lbs"]),
                "7-day Prior avg.": _cell(row["avg_7d_lbs"]),
                "Weekly weight gain/loss": _cell(row["weekly_change_lbs"]),
                "Body Fat(%)": _cell(row["body_fat_pct"]),
                "Notes": "",
            }
        )
    return out


def write_numbers_csv(rows: list[dict[str, str]], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Format a Renpho CSV for the Numbers weight sheet.")
    parser.add_argument("csv_path", type=Path, help="Renpho Health export")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Destination CSV (default: <input stem>-numbers.csv next to the input)",
    )
    args = parser.parse_args(argv)
    source = args.csv_path.expanduser()
    if not source.is_file():
        print(f"File not found: {source}", file=sys.stderr)
        return 1
    try:
        readings = readings_from_renpho(source)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not readings:
        print(f"No weigh-ins in {source}", file=sys.stderr)
        return 1
    sheet = numbers_rows(readings)
    dest = args.output.expanduser() if args.output else source.with_name(f"{source.stem}-numbers.csv")
    write_numbers_csv(sheet, dest)
    print(f"Wrote {len(sheet)} days to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
