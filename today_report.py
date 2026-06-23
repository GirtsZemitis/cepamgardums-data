#!/usr/bin/env python3
"""Per-machine sales for a given day (default: latest day in the book), broken down
by product and quantity. Reads orders.db (the sales book).

Run api_client.py first to refresh, then:
    ../.venv_orders/bin/python today_report.py            # latest day in the book
    ../.venv_orders/bin/python today_report.py --date 2026-06-19
"""
import argparse
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: latest in book)")
    args = ap.parse_args()

    con = sqlite3.connect(os.path.join(HERE, "orders.db"))
    con.row_factory = sqlite3.Row
    day = args.date or con.execute(
        "SELECT MAX(order_date) d FROM orders").fetchone()["d"]

    rows = con.execute("""
        SELECT machine_code, location, product_name,
               SUM(CAST(quantity AS INT)) qty, ROUND(SUM(price_eur),2) rev
        FROM orders
        WHERE order_date = ? AND shipping_status = 'Goods Shipped'
        GROUP BY machine_code, location, product_name
        ORDER BY machine_code, qty DESC
    """, (day,)).fetchall()
    con.close()

    if not rows:
        print(f"Nav pārdošanas datu {day}.")
        return

    print(f"\n  PĀRDOTS {day}\n  " + "=" * 46)
    machines, cur = {}, None
    g_qty = g_rev = 0
    for r in rows:
        machines.setdefault(r["machine_code"], (r["location"], []))[1].append(r)

    for mc, (loc, items) in machines.items():
        m_qty = sum(i["qty"] for i in items)
        m_rev = sum(i["rev"] for i in items)
        g_qty += m_qty
        g_rev += m_rev
        head = f"{loc}" if loc else f"Automāts {mc}"
        print(f"\n  {head}   —  {m_qty} gab. · €{m_rev:.2f}")
        for i in items:
            print(f"    {i['qty']:>3} ×  {i['product_name']}   (€{i['rev']:.2f})")

    print("\n  " + "-" * 46)
    print(f"  KOPĀ:  {g_qty} gab. · €{g_rev:.2f}   ({len(machines)} automāti)\n")


if __name__ == "__main__":
    main()
