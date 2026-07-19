#!/usr/bin/env python3
"""Consolidate fragmented vending-machine order exports into one clean dataset.

Reads every `Order+Details*.xls` in this folder, normalizes the columns,
de-duplicates by order number (the exports overlap / repeat days), and writes:

  orders.csv   — clean, deduplicated, one row per order line
  orders.db    — SQLite database (table `orders` + reference table `machines`)

Re-run any time you drop new .xls exports in this folder:
    ../.venv_orders/bin/python build_dataset.py
"""
import glob
import os
import sqlite3
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# Raw column order as exported (row 1 of each sheet is the header; data starts row 2).
RAW_COLS = [
    "merchant_id", "merchant_name", "machine_code", "machine_name", "order_number",
    "cargo_lane", "product_number", "product_name", "commodity_price", "original_price",
    "discounted_price", "delivery_time", "shipping_status", "purchaser", "refund_time",
    "remarks", "refund_status", "tpt_number", "tp_order_no", "payment_amount",
    "payment_time", "quantity",
]

# Optional: map vending-machine serials to human-readable locations once known.
# Leave blank ("") for unknown; fill in as you confirm which machine sits where.
MACHINE_LOCATIONS = {
    "2202000072": "2202000072 Jelgava",
    "2506000017": "2506000017 Spilve",
    "2506000018": "2506000018 Rīga",
    "2512000367": "2512000367 Jaunolaine",
    "2512000368": "2512000368 Mārupe",
}


def load_all():
    frames = []
    for f in sorted(glob.glob(os.path.join(HERE, "Order+Details*.xls"))):
        df = pd.read_excel(f, sheet_name=0, header=None, skiprows=2)
        df.columns = RAW_COLS
        df["source_file"] = os.path.basename(f)
        frames.append(df)
    if not frames:
        raise SystemExit("No Order+Details*.xls files found.")
    return pd.concat(frames, ignore_index=True)


def build():
    raw = load_all()
    n_raw = len(raw)

    # De-duplicate: exports overlap, but duplicate order_numbers are exact copies.
    # Keep the first occurrence per order_number.
    raw = raw.drop_duplicates(subset="order_number", keep="first").copy()

    # Normalize types / derived time fields for easy querying.
    raw["payment_time"] = pd.to_datetime(raw["payment_time"], errors="coerce")
    raw["order_date"] = raw["payment_time"].dt.strftime("%Y-%m-%d")
    raw["hour"] = raw["payment_time"].dt.hour
    raw["weekday"] = raw["payment_time"].dt.day_name()
    raw["machine_code"] = raw["machine_code"].astype(str)
    raw["location"] = raw["machine_code"].map(MACHINE_LOCATIONS).fillna("")

    # Canonical, de-noised schema. Dropped columns are constant/redundant in the data:
    #   merchant_* (always 3553/Ackord), machine_name (==machine_code),
    #   original_price & payment_amount (==commodity_price), discounted_price (0),
    #   purchaser/refund_*/remarks (empty), tpt_number/tp_order_no (==order_number).
    out = pd.DataFrame({
        "order_number": raw["order_number"].astype(str),
        "payment_time": raw["payment_time"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "order_date": raw["order_date"],
        "hour": raw["hour"],
        "weekday": raw["weekday"],
        "machine_code": raw["machine_code"],
        "location": raw["location"],
        "product_name": raw["product_name"],
        "product_number": raw["product_number"].astype(str),
        "cargo_lane": raw["cargo_lane"].astype(str),
        "price_eur": raw["commodity_price"].astype(float),
        "quantity": raw["quantity"].astype("Int64"),
        "shipping_status": raw["shipping_status"],
        "source_file": raw["source_file"],
    }).sort_values("payment_time").reset_index(drop=True)

    csv_path = os.path.join(HERE, "orders.csv")
    db_path = os.path.join(HERE, "orders.db")
    out.to_csv(csv_path, index=False)

    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    out.to_sql("orders", con, index=False)
    # Reference table for machine -> location enrichment.
    machines = (out.groupby(["machine_code", "location"], dropna=False)
                .size().reset_index(name="order_lines"))
    machines.to_sql("machines", con, index=False)
    con.executescript(
        "CREATE INDEX idx_orders_date ON orders(order_date);"
        "CREATE INDEX idx_orders_machine ON orders(machine_code);"
        "CREATE INDEX idx_orders_product ON orders(product_name);"
    )
    con.commit()

    # Report
    print(f"Raw rows read           : {n_raw}")
    print(f"Unique orders (kept)    : {len(out)}")
    print(f"Duplicates removed      : {n_raw - len(out)}")
    print(f"Date range              : {out['order_date'].min()} -> {out['order_date'].max()}")
    print(f"Machines                : {out['machine_code'].nunique()}")
    print(f"Products                : {out['product_name'].nunique()}")
    print(f"Total revenue (shipped) : EUR {out.loc[out.shipping_status=='Goods Shipped','price_eur'].sum():.2f}")
    print(f"\nWrote: {csv_path}")
    print(f"Wrote: {db_path}")
    con.close()


if __name__ == "__main__":
    build()
