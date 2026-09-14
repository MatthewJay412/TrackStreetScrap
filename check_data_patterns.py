"""
ONE-TIME CHECK -- not part of the pipeline. Run manually:
    python3 check_data_patterns.py

Looks at the FULL contents of each brand's downloaded CSV (not just a
sample) for the specific things that matter for the cleaning step:
UPC digit lengths, what values actually appear in the Yes/blank columns,
and whether any column is unexpectedly empty across the board.
"""

import pandas as pd
from pathlib import Path

import config

bool_ish_cols = ["Buy Box Winner", "Has Amazon Discount", "Out of Stock", "Price In Cart"]
price_cols = ["MAP", "Offer Price"]
price_pattern = r'^\$[\d,]+\.\d{2}$'
date_pattern = r'^\d{2}/\d{2}/\d{4} \d{1,2}:\d{2} [AP]M UTC$'

for path in sorted(config.DOWNLOAD_DIR.glob("*.csv")):
    print(f"\n{'=' * 60}\n{path.stem}\n{'=' * 60}")
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"UPC": str, "SKU": str})

    print(f"Rows: {len(df)}")

    print("\nUPC digit-length distribution:")
    print(df["UPC"].dropna().astype(str).str.len().value_counts())

    for col in bool_ish_cols:
        if col in df.columns:
            print(f"\n'{col}' actual values (including blanks):")
            print(df[col].fillna("<blank>").value_counts())

    for col in price_cols:
        if col in df.columns:
            bad = df[col].dropna().astype(str)
            bad = bad[~bad.str.match(price_pattern)]
            print(f"\n'{col}': {len(bad)} value(s) that DON'T match expected $X,XXX.XX format")
            if len(bad) > 0:
                print(bad.unique()[:10])

    if "Date/Time" in df.columns:
        bad = df["Date/Time"].dropna().astype(str)
        bad = bad[~bad.str.match(date_pattern)]
        print(f"\n'Date/Time': {len(bad)} value(s) that DON'T match expected format")
        if len(bad) > 0:
            print(bad.unique()[:10])

    empty_cols = [c for c in df.columns if df[c].isna().all()]
    if empty_cols:
        print(f"\nColumns that are 100% empty in this file: {empty_cols}")
