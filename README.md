# csvstats

⚡ Fast, zero-dependency CLI tool for instant CSV file statistics. Get column types, distributions, nulls, and numeric summaries in your terminal — no pandas required.

## Features

- **Column type detection** — automatically identifies numeric, boolean, datetime, and string columns
- **Null/empty tracking** — counts missing values per column
- **Numeric summaries** — min, max, mean, median, std dev for number columns
- **String stats** — min/max/avg length, unique value counts
- **Beautiful terminal output** — colored, aligned tables with Unicode box drawing
- **Zero dependencies** — pure Python 3.7+, no pip install needed
- **Fast** — streams large files without loading everything into memory

## Installation

### Direct use (no install needed)

```bash
chmod +x csvstats.py
./csvstats.py data.csv
```

### Via pip

```bash
pip install csvstats-cli
```

## Usage

```bash
# Basic stats for a CSV file
csvstats data.csv

# Limit to specific columns
csvstats data.csv --columns name,age,salary

# Show only first N rows sampled
csvstats data.csv --sample 1000

# JSON output for scripting
csvstats data.csv --json

# Show top N most frequent values per column
csvstats data.csv --top 5

# Quiet mode (just the summary table)
csvstats data.csv --quiet
```

## Example Output

```
╭──────────────────────────────────────────────────────────────╮
│                    📊 CSV Statistics                         │
│                    File: employees.csv                       │
│                    Rows: 10,000 | Columns: 7                 │
╰──────────────────────────────────────────────────────────────╯

┌──────────────┬──────────┬────────┬────────┬──────────────────────────────────┐
│ Column       │ Type     │ Nulls  │ Unique │ Summary                          │
├──────────────┼──────────┼────────┼────────┼──────────────────────────────────┤
│ name         │ string   │      0 │  9,847 │ len: 3–28, avg 14.2             │
│ age          │ integer  │     12 │     67 │ min=18 max=72 mean=38.4 med=36  │
│ salary       │ float    │     45 │  4,231 │ min=28k max=245k mean=72.3k     │
│ department   │ string   │      3 │     12 │ len: 3–18, avg 8.7              │
│ active       │ boolean  │      0 │      2 │ true: 7,812 (78.1%)             │
│ hire_date    │ datetime │      8 │  2,847 │ 2015-03-12 → 2024-11-30         │
│ email        │ string   │     21 │  9,979 │ len: 8–42, avg 24.1             │
└──────────────┴──────────┴────────┴────────┴──────────────────────────────────┘
```

## How It Works

1. **Streams the file** — reads CSV row by row, never loads entire file
2. **Samples for type detection** — examines first 1,000 rows to infer column types
3. **Accumulates stats** — running min/max/mean/variance via Welford's algorithm
4. **Resolves final types** — promotes `integer` → `float` if any row has decimals

## Supported CSV Formats

- Standard RFC 4180 CSV
- Tab-separated (`.tsv`) — auto-detected
- Semicolon-separated — auto-detected
- Custom delimiter via `--delimiter`
- Handles quoted fields, escaped quotes, multiline values
- UTF-8 and Latin-1 encoding auto-detection

## Requirements

Python 3.7+ (uses only stdlib: `csv`, `statistics`, `json`, `collections`)

## License

MIT
