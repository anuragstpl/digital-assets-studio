"""Royalty maths and the store pack.

The pricing table is the part people get wrong: KDP's 70% band only applies
inside a price window and charges a delivery fee per megabyte, so a big
illustrated file can earn less at 70% than at 35%. This computes both and says
which wins.

Rates verified against KDP's published terms as of July 2026. They do change -
STORE_RULES is the one place to update when they do.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

STORE_RULES = {
    "kdp_70_min": 2.99,
    "kdp_70_max": 12.99,          # raised from 9.99 on 7 July 2026
    "kdp_delivery_per_mb": 0.15,
    "kdp_35_rate": 0.35,
    "kdp_70_rate": 0.70,
    "kdp_print_royalty_rate": 0.60,
    "kdp_print_fixed_bw": 0.85,   # per-book fixed cost, US marketplace, b&w
    "kdp_print_per_page_bw": 0.012,
    "gumroad_rate": 0.10,         # platform fee on Gumroad's current flat plan
    "gumroad_payment_rate": 0.029,
    "gumroad_payment_fixed": 0.30,
    "payhip_free_rate": 0.05,
    "etsy_listing_fee": 0.20,
    "etsy_transaction_rate": 0.065,
    "etsy_payment_rate": 0.03,
    "etsy_payment_fixed": 0.25,
}


@dataclass
class PriceRow:
    channel: str
    list_price: float
    fees: float
    net: float
    note: str = ""

    @property
    def margin(self) -> float:
        return self.net / self.list_price if self.list_price else 0.0


def kdp_ebook(price: float, file_mb: float) -> list[PriceRow]:
    rows: list[PriceRow] = []
    r = STORE_RULES
    delivery = file_mb * r["kdp_delivery_per_mb"]
    if r["kdp_70_min"] <= price <= r["kdp_70_max"]:
        net70 = price * r["kdp_70_rate"] - delivery
        rows.append(PriceRow("Amazon KDP - 70% band", price, price - net70, round(net70, 2),
                             f"delivery fee {delivery:.2f} on a {file_mb:.1f} MB file"))
    else:
        rows.append(PriceRow("Amazon KDP - 70% band", price, 0.0, 0.0,
                             f"not available: 70% only applies between "
                             f"${r['kdp_70_min']:.2f} and ${r['kdp_70_max']:.2f}"))
    net35 = price * r["kdp_35_rate"]
    rows.append(PriceRow("Amazon KDP - 35% band", price, price - net35, round(net35, 2),
                         "no delivery fee, available at any price"))
    return rows


def kdp_paperback(price: float, pages: int, colour: bool = False) -> PriceRow:
    r = STORE_RULES
    per_page = 0.0425 if colour else r["kdp_print_per_page_bw"]
    fixed = 1.00 if colour else r["kdp_print_fixed_bw"]
    printing = fixed + pages * per_page
    net = price * r["kdp_print_royalty_rate"] - printing
    return PriceRow("Amazon KDP - paperback", price, round(price - net, 2), round(net, 2),
                    f"printing cost ${printing:.2f} for {pages} pages"
                    + ("" if net > 0 else "  ← price is below the minimum that covers printing"))


def direct_channels(price: float) -> list[PriceRow]:
    r = STORE_RULES
    gum_fees = price * (r["gumroad_rate"] + r["gumroad_payment_rate"]) + r["gumroad_payment_fixed"]
    pay_fees = price * (r["payhip_free_rate"] + r["gumroad_payment_rate"]) + r["gumroad_payment_fixed"]
    etsy_fees = (r["etsy_listing_fee"] + price * (r["etsy_transaction_rate"] + r["etsy_payment_rate"])
                 + r["etsy_payment_fixed"])
    return [
        PriceRow("Gumroad", price, round(gum_fees, 2), round(price - gum_fees, 2), "10% + card fees"),
        PriceRow("Payhip (free plan)", price, round(pay_fees, 2), round(price - pay_fees, 2), "5% + card fees"),
        PriceRow("Etsy digital download", price, round(etsy_fees, 2), round(price - etsy_fees, 2),
                 "listing + transaction + payment fees"),
    ]


def pricing_table(ebook_price: float, print_price: float, file_mb: float, pages: int) -> list[PriceRow]:
    rows = kdp_ebook(ebook_price, file_mb)
    rows.append(kdp_paperback(print_price, pages))
    rows.extend(direct_channels(ebook_price))
    return rows


def recommend_price(file_mb: float, fiction: bool, pages: int) -> tuple[float, float, str]:
    """Return (ebook, paperback, reasoning)."""
    r = STORE_RULES
    ebook = 4.99 if fiction else 7.99
    if file_mb > 8:
        ebook = max(ebook, 6.99)
    breakeven = (r["kdp_print_fixed_bw"] + pages * r["kdp_print_per_page_bw"]) / r["kdp_print_royalty_rate"]
    # cover printing, leave a real margin, then land on a .99 price point
    paperback = round(max(breakeven + 4.0, 9.0)) - 0.01
    why = (
        f"{'Fiction sells on volume and Kindle Unlimited page reads, so $4.99 is the sweet spot. ' if fiction else 'Non-fiction carries a higher price when it solves a costed problem. '}"
        f"A {file_mb:.1f} MB file costs ${file_mb * r['kdp_delivery_per_mb']:.2f} per sale in delivery fees. "
        f"Paperback printing alone costs ${r['kdp_print_fixed_bw'] + pages * r['kdp_print_per_page_bw']:.2f} "
        f"at {pages} pages, so anything under ${breakeven:.2f} loses money."
    )
    return ebook, paperback, why


def markdown_table(rows: list[PriceRow]) -> str:
    out = ["| Channel | List | Fees | You keep | Margin | Notes |",
           "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        out.append(f"| {r.channel} | ${r.list_price:.2f} | ${r.fees:.2f} | "
                   f"**${r.net:.2f}** | {r.margin * 100:.0f}% | {r.note} |")
    return "\n".join(out)


# ------------------------------------------------------------------- pack ---

def build_pack(project_dir: Path, out_zip: Path, files: list[Path], manifest: dict) -> Path:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                z.write(f, arcname=f.name)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return out_zip
