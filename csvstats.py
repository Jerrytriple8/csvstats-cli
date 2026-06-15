#!/usr/bin/env python3
"""
csvstats — Fast, zero-dependency CSV statistics CLI.

Get column types, distributions, null counts, and numeric summaries
from CSV files directly in your terminal. No pandas required.

Usage:
    csvstats data.csv
    csvstats data.csv --columns name,age --top 5
    csvstats data.csv --json
"""

__version__ = "1.0.0"

import csv
import io
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


# ── Type Detection ──────────────────────────────────────────────

INTEGER_RE = __import__("re").compile(r"^-?\d+$")
FLOAT_RE = __import__("re").compile(r"^-?\d+\.\d+$")
BOOL_VALUES = {"true", "false", "yes", "no", "1", "0", "t", "f", "y", "n"}
DATETIME_HINTS = [
    __import__("re").compile(r"^\d{4}-\d{2}-\d{2}"),
    __import__("re").compile(r"^\d{2}/\d{2}/\d{4}"),
    __import__("re").compile(r"^\d{2}-\d{2}-\d{4}"),
]


def detect_type(values: List[str]) -> str:
    """Infer column type from a sample of non-empty string values."""
    if not values:
        return "empty"

    bool_count = 0
    int_count = 0
    float_count = 0
    dt_count = 0
    total = len(values)

    for v in values:
        vl = v.lower().strip()
        if vl in BOOL_VALUES:
            bool_count += 1
            continue
        if INTEGER_RE.match(vl):
            int_count += 1
            continue
        if FLOAT_RE.match(vl):
            float_count += 1
            continue
        for pat in DATETIME_HINTS:
            if pat.match(vl):
                dt_count += 1
                break

    threshold = total * 0.8
    if bool_count >= threshold:
        return "boolean"
    if int_count >= threshold:
        return "integer"
    if (int_count + float_count) >= threshold:
        return "float"
    if dt_count >= threshold:
        return "datetime"
    return "string"


# ── Statistics Accumulator ──────────────────────────────────────

class ColumnStats:
    """Running statistics for a single column using Welford's algorithm."""

    def __init__(self, name: str):
        self.name = name
        self.total = 0
        self.null_count = 0
        self.values_seen = 0

        # For numeric columns (Welford's online algorithm)
        self._mean = 0.0
        self._m2 = 0.0
        self._min = float("inf")
        self._max = float("-inf")
        self._all_values: List[float] = []  # kept small for median

        # For string columns
        self._str_lengths: List[int] = []
        self._str_min_len = float("inf")
        self._str_max_len = 0
        self._str_sum_len = 0

        # For boolean columns
        self._true_count = 0

        # For datetime columns
        self._dt_min: Optional[str] = None
        self._dt_max: Optional[str] = None

        # Unique values (capped at 100k to avoid memory blowup)
        self._uniques: set = set()
        self._unique_overflow = False

        # Top values tracking
        self._value_counts: Counter = Counter()

    def update(self, raw: str, col_type: str):
        self.total += 1
        stripped = raw.strip()

        if not stripped:
            self.null_count += 1
            return

        self.values_seen += 1

        # Track unique values (cap at 100k)
        if not self._unique_overflow:
            if len(self._uniques) < 100_000:
                self._uniques.add(stripped)
            else:
                self._unique_overflow = True
                self._uniques = set()  # free memory

        # Type-specific stats
        if col_type in ("integer", "float"):
            try:
                val = float(stripped)
            except ValueError:
                return

            self._all_values.append(val)
            self._min = min(self._min, val)
            self._max = max(self._max, val)

            # Welford's online variance
            self.values_seen_n = len(self._all_values)
            delta = val - self._mean
            self._mean += delta / self.values_seen_n
            delta2 = val - self._mean
            self._m2 += delta * delta2

        elif col_type == "string":
            length = len(stripped)
            self._str_lengths.append(length)
            self._str_min_len = min(self._str_min_len, length)
            self._str_max_len = max(self._str_max_len, length)
            self._str_sum_len += length

        elif col_type == "boolean":
            if stripped.lower() in ("true", "yes", "1", "t", "y"):
                self._true_count += 1

        elif col_type == "datetime":
            if self._dt_min is None or stripped < self._dt_min:
                self._dt_min = stripped
            if self._dt_max is None or stripped > self._dt_max:
                self._dt_max = stripped

        # Track top values (cap counter size)
        if len(self._value_counts) < 50_000:
            self._value_counts[stripped] += 1

    @property
    def unique_count(self) -> int:
        if self._unique_overflow:
            return -1  # unknown
        return len(self._uniques)

    def numeric_summary(self) -> Dict[str, Any]:
        n = len(self._all_values)
        if n == 0:
            return {}
        result = {
            "min": self._min,
            "max": self._max,
            "mean": round(self._mean, 4),
            "count": n,
        }
        if n >= 2:
            variance = self._m2 / (n - 1)
            result["std_dev"] = round(math.sqrt(variance), 4)
        if n > 0:
            sorted_vals = sorted(self._all_values)
            mid = n // 2
            if n % 2 == 0:
                result["median"] = round((sorted_vals[mid - 1] + sorted_vals[mid]) / 2, 4)
            else:
                result["median"] = round(sorted_vals[mid], 4)
        return result

    def string_summary(self) -> Dict[str, Any]:
        n = len(self._str_lengths)
        if n == 0:
            return {}
        return {
            "min_len": self._str_min_len,
            "max_len": self._str_max_len,
            "avg_len": round(self._str_sum_len / n, 1),
            "count": n,
        }

    def boolean_summary(self) -> Dict[str, Any]:
        total = self.values_seen
        if total == 0:
            return {}
        return {
            "true_count": self._true_count,
            "false_count": total - self._true_count,
            "true_pct": round(100 * self._true_count / total, 1),
        }

    def datetime_summary(self) -> Dict[str, Any]:
        return {"min": self._dt_min, "max": self._dt_max}

    def top_values(self, n: int = 5) -> List[Tuple[str, int]]:
        return self._value_counts.most_common(n)


# ── CSV Analyzer ────────────────────────────────────────────────

class CSVAnalyzer:
    """Stream a CSV file and compute per-column statistics."""

    def __init__(self, filepath: str, delimiter: Optional[str] = None,
                 sample_rows: int = 1000, columns: Optional[List[str]] = None):
        self.filepath = filepath
        self.delimiter = delimiter
        self.sample_rows = sample_rows
        self.filter_columns = columns

        self.headers: List[str] = []
        self.col_types: Dict[str, str] = {}
        self.stats: Dict[str, ColumnStats] = {}
        self.total_rows = 0
        self.file_size = 0

    def _detect_delimiter(self, sample: str) -> str:
        if self.delimiter:
            return self.delimiter
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return ","

    def _detect_encoding(self) -> str:
        """Try UTF-8 first, fall back to Latin-1."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                f.read(8192)
            return "utf-8"
        except UnicodeDecodeError:
            return "latin-1"

    def analyze(self) -> Dict[str, ColumnStats]:
        encoding = self._detect_encoding()
        self.file_size = os.path.getsize(self.filepath)

        # Phase 1: Sample for type detection
        sample_values: Dict[str, List[str]] = defaultdict(list)
        sample_count = 0

        with open(self.filepath, "r", encoding=encoding, errors="replace") as f:
            head = f.read(8192)
            f.seek(0)

            delimiter = self._detect_delimiter(head)
            reader = csv.reader(f, delimiter=delimiter)

            for i, row in enumerate(reader):
                if i == 0:
                    self.headers = [h.strip() for h in row]
                    for h in self.headers:
                        sample_values[h] = []
                    continue

                if sample_count < self.sample_rows:
                    for j, val in enumerate(row):
                        if j < len(self.headers):
                            sample_values[self.headers[j]].append(val.strip())
                    sample_count += 1
                else:
                    break

        # Detect types from sample
        for h in self.headers:
            self.col_types[h] = detect_type(sample_values[h])

        # Initialize stats
        for h in self.headers:
            self.stats[h] = ColumnStats(h)

        # Phase 2: Full pass
        with open(self.filepath, "r", encoding=encoding, errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            next(reader, None)  # skip header

            for row in reader:
                self.total_rows += 1
                for j, val in enumerate(row):
                    if j < len(self.headers):
                        h = self.headers[j]
                        if self.filter_columns and h not in self.filter_columns:
                            continue
                        self.stats[h].update(val, self.col_types[h])

        return self.stats


# ── Formatting ──────────────────────────────────────────────────

def _fmt_number(n: float) -> str:
    """Format a number compactly (e.g. 72300 -> '72.3k')."""
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    if n == int(n):
        return str(int(n))
    return f"{n:.2f}"


def _color(text: str, code: str) -> str:
    """ANSI color wrapper."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(text: str) -> str:
    return _color(text, "1")


def _dim(text: str) -> str:
    return _color(text, "2")


def _cyan(text: str) -> str:
    return _color(text, "36")


def _green(text: str) -> str:
    return _color(text, "32")


def _yellow(text: str) -> str:
    return _color(text, "33")


def _red(text: str) -> str:
    return _color(text, "31")


def _magenta(text: str) -> str:
    return _color(text, "35")


TYPE_COLORS = {
    "integer": "36",   # cyan
    "float": "36",     # cyan
    "boolean": "33",   # yellow
    "datetime": "35",  # magenta
    "string": "32",    # green
    "empty": "2",      # dim
}


def format_summary_line(col_type: str, stats: ColumnStats) -> str:
    """Generate a one-line summary for a column."""
    if col_type in ("integer", "float"):
        ns = stats.numeric_summary()
        if not ns:
            return ""
        parts = [f"min={_fmt_number(ns['min'])}", f"max={_fmt_number(ns['max'])}",
                 f"mean={_fmt_number(ns['mean'])}"]
        if "median" in ns:
            parts.append(f"med={_fmt_number(ns['median'])}")
        return " ".join(parts)

    if col_type == "string":
        ss = stats.string_summary()
        if not ss:
            return ""
        return f"len: {ss['min_len']}–{ss['max_len']}, avg {ss['avg_len']}"

    if col_type == "boolean":
        bs = stats.boolean_summary()
        if not bs:
            return ""
        return f"true: {bs['true_count']:,} ({bs['true_pct']}%)"

    if col_type == "datetime":
        ds = stats.datetime_summary()
        if not ds or not ds["min"]:
            return ""
        return f"{ds['min']} → {ds['max']}"

    return ""


def print_table(analyzer: CSVAnalyzer, top_n: int = 0):
    """Print the stats table to terminal."""
    filename = os.path.basename(analyzer.filepath)

    # Header box
    w = 70
    print()
    print(_bold("╭" + "─" * w + "╮"))
    print(_bold("│") + "  📊 CSV Statistics".center(w) + _bold("│"))
    print(_bold("│") + f"  File: {filename}".center(w) + _bold("│"))
    print(_bold("│") + f"  Rows: {analyzer.total_rows:,} | Columns: {len(analyzer.headers)} | Size: {_fmt_number(analyzer.file_size)}B".center(w) + _bold("│"))
    print(_bold("╰" + "─" * w + "╯"))
    print()

    # Column widths
    col_w = max(len(h) for h in analyzer.headers) + 2
    type_w = 10
    null_w = 8
    uniq_w = 8
    sum_w = 55

    # Header row
    hdr = (f"  {'Column':<{col_w}}{'Type':<{type_w}}{'Nulls':>{null_w}}"
           f"{'Unique':>{uniq_w}}  {'Summary':<{sum_w}}")
    print(_bold(hdr))
    print("  " + "─" * (col_w + type_w + null_w + uniq_w + sum_w + 2))

    # Data rows
    for h in analyzer.headers:
        s = analyzer.stats[h]
        ct = analyzer.col_types[h]

        type_str = f"  {ct:<8}"
        type_colored = _color(type_str, TYPE_COLORS.get(ct, "0"))

        null_str = f"{s.null_count:>7,}"
        if s.null_count > 0:
            null_str = _yellow(null_str)
        else:
            null_str = _dim(null_str)

        uniq = s.unique_count
        uniq_str = f"{uniq:>7,}" if uniq >= 0 else "  >100k"

        summary = format_summary_line(ct, s)

        line = f"  {h:<{col_w}}{type_colored}{null_str}{uniq_str}  {summary}"
        print(line)

    # Top values
    if top_n > 0:
        print()
        print(_bold("  Top Values:"))
        print("  " + "─" * 60)
        for h in analyzer.headers:
            tops = analyzer.stats[h].top_values(top_n)
            if not tops:
                continue
            vals = ", ".join(f"{v} ({c:,})" for v, c in tops)
            truncated = vals[:80] + ("…" if len(vals) > 80 else "")
            print(f"  {_cyan(h)}: {truncated}")

    print()


def print_json(analyzer: CSVAnalyzer):
    """Output stats as JSON."""
    result = {
        "file": analyzer.filepath,
        "rows": analyzer.total_rows,
        "columns": len(analyzer.headers),
        "file_size_bytes": analyzer.file_size,
        "stats": {},
    }

    for h in analyzer.headers:
        s = analyzer.stats[h]
        ct = analyzer.col_types[h]
        col = {
            "type": ct,
            "nulls": s.null_count,
            "unique": s.unique_count if s.unique_count >= 0 else None,
        }

        if ct in ("integer", "float"):
            col["numeric"] = s.numeric_summary()
        elif ct == "string":
            col["string"] = s.string_summary()
        elif ct == "boolean":
            col["boolean"] = s.boolean_summary()
        elif ct == "datetime":
            col["datetime"] = s.datetime_summary()

        result["stats"][h] = col

    print(json.dumps(result, indent=2, default=str))


# ── CLI ─────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="csvstats",
        description="⚡ Fast CSV file statistics — column types, nulls, numeric summaries.",
        epilog="Examples:\n"
               "  csvstats data.csv\n"
               "  csvstats data.csv --columns name,age --top 5\n"
               "  csvstats data.csv --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", help="CSV file to analyze")
    parser.add_argument("--columns", "-c",
                        help="Comma-separated list of columns to analyze (default: all)")
    parser.add_argument("--delimiter", "-d",
                        help="CSV delimiter (default: auto-detect)")
    parser.add_argument("--sample", "-s", type=int, default=1000,
                        help="Rows to sample for type detection (default: 1000)")
    parser.add_argument("--top", "-t", type=int, default=0,
                        help="Show top N most frequent values per column")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress the header box")
    parser.add_argument("--version", "-V", action="version",
                        version=f"csvstats {__version__}")

    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    columns = None
    if args.columns:
        columns = [c.strip() for c in args.columns.split(",")]

    analyzer = CSVAnalyzer(
        filepath=args.file,
        delimiter=args.delimiter,
        sample_rows=args.sample,
        columns=columns,
    )

    analyzer.analyze()

    if args.json:
        print_json(analyzer)
    else:
        print_table(analyzer, top_n=args.top)


if __name__ == "__main__":
    main()
