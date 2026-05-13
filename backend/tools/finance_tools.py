"""
Finans ajanı için SQL tabanlı araçlar (maliyet/satış DB'den; kâr için sabit maliyet oranı kullanılır).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from database import fetch_all
from . import order_tools

# DB'de birim maliyet yok; brüt kâr için satır gelirine uygulanan tahmini maliyet oranı (demo).
_ESTIMATED_COST_RATIO = 0.52


async def get_daily_revenue(
    date_str: str | None = None,
    kooperatif_id: int | None = None,
) -> dict[str, Any]:
    """Belirli bir günün sipariş cirosu ve adedi (iptaller hariç)."""
    r = await order_tools.get_daily_report(date_str, kooperatif_id=kooperatif_id)
    oz = r.get("ozet") or {}
    return {
        "date": r["tarih"],
        "total_revenue": float(oz.get("toplam_ciro") or 0),
        "order_count": int(oz.get("siparis_sayisi") or 0),
    }


async def get_weekly_summary(kooperatif_id: int | None = None) -> dict[str, Any]:
    """Son 7 takvim günü günlük kırılım + toplamlar."""
    daily_breakdown: list[dict[str, Any]] = []
    total_rev = 0.0
    total_orders = 0
    for i in range(7):
        d = (date.today() - timedelta(days=i)).isoformat()
        r = await order_tools.get_daily_report(d, kooperatif_id=kooperatif_id)
        oz = r.get("ozet") or {}
        rev = float(oz.get("toplam_ciro") or 0)
        oc = int(oz.get("siparis_sayisi") or 0)
        daily_breakdown.append(
            {"date": r["tarih"], "revenue": rev, "order_count": oc}
        )
        total_rev += rev
        total_orders += oc
    daily_breakdown.reverse()
    return {
        "total_revenue": total_rev,
        "total_orders": total_orders,
        "daily_breakdown": daily_breakdown,
    }


async def get_top_selling_products(
    limit: int = 10,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """Son 30 günde en çok satan ürünler (iptal siparişler hariç)."""
    params: list[Any] = [limit]
    where_extra = ""
    if kooperatif_id is not None:
        where_extra = " AND u.kooperatif_id = %s"
        params.insert(0, kooperatif_id)

    rows = await fetch_all(
        f"""
        SELECT u.ad AS product_name,
               SUM(sk.miktar)::bigint AS total_quantity_sold,
               SUM(sk.miktar * sk.birim_fiyat)::float AS total_revenue
        FROM siparis_kalemleri sk
        JOIN siparisler s ON s.id = sk.siparis_id
        JOIN urunler u ON u.id = sk.urun_id
        WHERE s.olusturulma >= NOW() - INTERVAL '30 days'
          AND s.durum != 'iptal'
          {where_extra}
        GROUP BY u.id, u.ad
        ORDER BY total_quantity_sold DESC
        LIMIT %s
        """,
        tuple(params),
    )
    out: list[dict[str, Any]] = []
    for row in rows or []:
        out.append(
            {
                "product_name": row["product_name"],
                "total_quantity_sold": int(row["total_quantity_sold"] or 0),
                "total_revenue": float(row["total_revenue"] or 0),
            }
        )
    return out


async def get_profit_by_product(
    limit: int = 15,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Son 90 gün ürün bazlı gelir; maliyet tahmini sabit oranla (veritabanında maliyet kolonu yok).
    """
    params: list[Any] = [limit]
    where_extra = ""
    if kooperatif_id is not None:
        where_extra = " AND u.kooperatif_id = %s"
        params.insert(0, kooperatif_id)

    rows = await fetch_all(
        f"""
        SELECT u.ad AS product_name,
               SUM(sk.miktar * sk.birim_fiyat)::float AS total_revenue
        FROM siparis_kalemleri sk
        JOIN siparisler s ON s.id = sk.siparis_id
        JOIN urunler u ON u.id = sk.urun_id
        WHERE s.olusturulma >= NOW() - INTERVAL '90 days'
          AND s.durum != 'iptal'
          {where_extra}
        GROUP BY u.id, u.ad
        HAVING SUM(sk.miktar * sk.birim_fiyat) > 0
        ORDER BY total_revenue DESC
        LIMIT %s
        """,
        tuple(params),
    )
    result: list[dict[str, Any]] = []
    for row in rows or []:
        rev = float(row["total_revenue"] or 0)
        cost = rev * _ESTIMATED_COST_RATIO
        result.append(
            {
                "product_name": row["product_name"],
                "total_revenue": rev,
                "total_cost": round(cost, 2),
                "gross_profit": round(rev - cost, 2),
            }
        )
    return result


async def get_pending_expenses(
    limit: int = 20,
    kooperatif_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Son gider kayıtları; 'vade' olarak kayıt tarihine +14 gün tahmini vade atanır.
    """
    if kooperatif_id is not None:
        rows = await fetch_all(
            """
            SELECT id, tarih, tutar, aciklama, kategori
            FROM muhasebe
            WHERE tur = 'gider' AND kooperatif_id = %s
            ORDER BY tarih DESC
            LIMIT %s
            """,
            (kooperatif_id, limit),
        )
    else:
        rows = await fetch_all(
            """
            SELECT id, tarih, tutar, aciklama, kategori
            FROM muhasebe
            WHERE tur = 'gider'
            ORDER BY tarih DESC
            LIMIT %s
            """,
            (limit,),
        )
    today = date.today()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        tarih = row["tarih"]
        if hasattr(tarih, "date"):
            tarih = tarih.date()
        due = tarih + timedelta(days=14)
        desc = (row.get("aciklama") or "").strip() or str(row.get("kategori") or "Gider")
        out.append(
            {
                "description": desc,
                "amount": float(row["tutar"] or 0),
                "due_date": due.isoformat(),
                "days_until_due": (due - today).days,
            }
        )
    return out


FINANCE_TOOLS: dict[str, Any] = {
    "get_daily_revenue": get_daily_revenue,
    "get_weekly_summary": get_weekly_summary,
    "get_top_selling_products": get_top_selling_products,
    "get_profit_by_product": get_profit_by_product,
    "get_pending_expenses": get_pending_expenses,
}
