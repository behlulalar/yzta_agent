"""
Gemini ve OpenAI için ortak tool şemaları ve dispatcher.
İş mantığı yok; yalnızca şema + fonksiyon eşlemesi.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

from tools import order_tools, product_tools, rag_tools

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_products": {
        "name": "search_products",
        "description": (
            "Veritabanında ürün arar. Müşteri ürün soruyor, öneri istiyor veya "
            "'var mı' diyorsa MUTLAKA bunu kullan. Türkçe cümlelerde ana kelimeyi "
            "`query` olarak geçir (örn. 'Bal ürünlerinden önerir misin?' → query='bal'; "
            "'kahvaltılık' → query veya kategori). Kategori kesinse `kategori` de kullanılabilir. "
            "Kooperatif adı geçiyorsa `kooperatif_ad` ile ara (örn. 'Horanta'). "
            "Sonuç dönmeden 'ürün yok' denmez."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Ürün adı veya açıklamada aranacak kelime."},
                "kategori": {"type": "string", "description": "Kategori adı (örn. 'Bal', 'Tekstil')."},
                "min_fiyat": {
                    "type": "number",
                    "description": "Minimum fiyat filtresi, '500 TL üstü' gibi sorgular için.",
                },
                "max_fiyat": {"type": "number", "description": "Bu fiyatın altındaki ürünleri getir."},
                "kooperatif_ad": {
                    "type": "string",
                    "description": "Kooperatif adında geçen metin (ILIKE; örn. 'Horanta').",
                },
                "limit": {"type": "integer", "description": "Maksimum sonuç sayısı (varsayılan 10)."},
            },
        },
    },
    "get_product_detail": {
        "name": "get_product_detail",
        "description": "Bir ürünün tüm bilgilerini ve üretici kooperatifi döndürür.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "Ürün ID'si."},
            },
            "required": ["product_id"],
        },
    },
    "list_categories": {
        "name": "list_categories",
        "description": "Mevcut tüm ürün kategorilerini ve her birindeki ürün sayısını döndürür.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_low_stock": {
        "name": "get_low_stock",
        "description": (
            "Stoğu kritik eşiğin altında olan ürünleri listeler. "
            "Satıcı moduna özel; kritik stok uyarısı için kullanılır."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kooperatif_id": {"type": "integer", "description": "Belirli kooperatif filtresi."},
                "limit": {"type": "integer", "description": "Maks sonuç (varsayılan 50)."},
            },
        },
    },
    "get_stock_by_name": {
        "name": "get_stock_by_name",
        "description": "Ürün adına göre güncel stok miktarını sorgular.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "Aranacak ürün adı."},
            },
            "required": ["product_name"],
        },
    },
    "get_order": {
        "name": "get_order",
        "description": "Sipariş numarasıyla siparişin durumunu, müşterisini ve özetini döndürür.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "description": "Sipariş ID'si."},
            },
            "required": ["order_id"],
        },
    },
    "get_order_items": {
        "name": "get_order_items",
        "description": "Bir siparişteki ürün kalemlerini (ürün adı, miktar, fiyat) listeler.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "description": "Sipariş ID'si."},
            },
            "required": ["order_id"],
        },
    },
    "get_shipping_info": {
        "name": "get_shipping_info",
        "description": "Bir siparişin kargo firması, takip numarası ve kargo durumunu döndürür.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "description": "Sipariş ID'si."},
            },
            "required": ["order_id"],
        },
    },
    "list_recent_orders": {
        "name": "list_recent_orders",
        "description": "Son siparişleri listeler; opsiyonel olarak duruma göre filtrelenir.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Kaç tane (varsayılan 10)."},
                "durum": {
                    "type": "string",
                    "description": "'beklemede' | 'hazırlanıyor' | 'kargoda' | 'teslim edildi' | 'iptal'",
                },
            },
        },
    },
    "search_orders_by_customer": {
        "name": "search_orders_by_customer",
        "description": "Müşteri email veya adıyla siparişleri arar.",
        "parameters": {
            "type": "object",
            "properties": {
                "musteri_email": {"type": "string"},
                "musteri_ad": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    "get_product_for_order": {
        "name": "get_product_for_order",
        "description": (
            "Yeni sipariş için ürün ara: stokta olan tek satır döner. "
            "Sadece sipariş oluşturma akışında kullan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urun_adi": {"type": "string", "description": "Ürün adı veya parça metin"},
            },
            "required": ["urun_adi"],
        },
    },
    "create_order": {
        "name": "create_order",
        "description": (
            "Ürün için sipariş oluşturur, kalemi yazar ve stoğu düşer (demo ödeme URL döner). "
            "Yalnızca adres ve miktar doğrulandıktan sonra."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urun_id": {"type": "integer"},
                "miktar": {"type": "integer"},
                "musteri_adi": {"type": "string"},
                "adres": {"type": "string"},
            },
            "required": ["urun_id", "miktar", "musteri_adi", "adres"],
        },
    },
    "get_daily_report": {
        "name": "get_daily_report",
        "description": (
            "Belirli bir gün için satış raporu: ciro, sipariş sayısı, en çok satan 5 ürün. "
            "target_date verilmezse bugün."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "YYYY-MM-DD biçiminde tarih."},
            },
        },
    },
    "semantic_search_urunler": {
        "name": "semantic_search_urunler",
        "description": (
            "Anlamlı/semantik ürün araması için kullan. Şu durumlarda bu tool'u çağır: "
            "hediye önerisi ('anneler günü hediyesi', 'doğum günü için ne alayım'); "
            "yöresel/organik/el yapımı gibi özellik araması; kategori net değil "
            "('tatlı bir şey', 'sağlıklı atıştırmalık'); bölge bazlı arama "
            "('Hakkari'den ürün', 'Ege lezzetleri'); kullanım amacı ('kahvaltı için', "
            "'çocuklar için'). "
            "search_products ile farkı: kesin ürün adı biliniyorsa ('bal', 'tahin') "
            "search_products; niyet/özellik/amaç varsa semantic_search_urunler."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kullanıcının isteği veya arama niyeti (doğal dil).",
                },
                "kategori": {"type": "string", "description": "Opsiyonel; net kategori varsa."},
                "bolge": {"type": "string", "description": "Opsiyonel; bölge filtresi (örn. Hakkari)."},
                "min_fiyat": {
                    "type": "number",
                    "description": "Minimum fiyat filtresi, '500 TL üstü' gibi sorgular için.",
                },
                "max_fiyat": {"type": "number", "description": "Opsiyonel bütçe üst sınırı."},
            },
        },
    },
    "semantic_search_kooperatifler": {
        "name": "semantic_search_kooperatifler",
        "description": (
            "Kooperatif hikayelerinde semantik arama. Şu durumlarda kullan: "
            "'Bu ürünü kim üretiyor?', kooperatif hikayesi/geçmişi soruları, "
            "'Sertifikalı kooperatif var mı?' gibi sorgular."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Kooperatif veya hikaye ile ilgili doğal dil sorgusu.",
                },
            },
            "required": ["query"],
        },
    },
}

DISPATCHER: dict[str, Callable[..., Awaitable[Any]]] = {
    "search_products": product_tools.search_products,
    "semantic_search_urunler": rag_tools.semantic_search_urunler,
    "semantic_search_kooperatifler": rag_tools.semantic_search_kooperatifler,
    "get_product_detail": product_tools.get_product_detail,
    "list_categories": product_tools.list_categories,
    "get_low_stock": product_tools.get_low_stock,
    "get_stock_by_name": product_tools.get_stock_by_name,
    "get_order": order_tools.get_order,
    "get_order_items": order_tools.get_order_items,
    "get_shipping_info": order_tools.get_shipping_info,
    "list_recent_orders": order_tools.list_recent_orders,
    "search_orders_by_customer": order_tools.search_orders_by_customer,
    "get_product_for_order": order_tools.get_product_for_order,
    "create_order": order_tools.create_order,
    "get_daily_report": order_tools.get_daily_report,
}

CUSTOMER_TOOLS = [
    "search_products",
    "semantic_search_urunler",
    "semantic_search_kooperatifler",
    "get_product_detail",
    "list_categories",
    "get_order",
    "get_order_items",
    "get_shipping_info",
    "search_orders_by_customer",
    "get_product_for_order",
    "create_order",
]

SELLER_TOOLS = [
    "search_products",
    "list_categories",
    "get_low_stock",
    "get_stock_by_name",
    "list_recent_orders",
    "get_daily_report",
    "get_product_detail",
]


def tool_names_for_mode(mode: str) -> list[str]:
    if (mode or "").lower() in ("satici", "seller", "vendor"):
        return [n for n in SELLER_TOOLS if n in TOOL_SCHEMAS]
    return [n for n in CUSTOMER_TOOLS if n in TOOL_SCHEMAS]


def openai_tools_for_mode(mode: str) -> list[dict[str, Any]]:
    specs = []
    for name in tool_names_for_mode(mode):
        sch = TOOL_SCHEMAS[name]
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": sch["name"],
                    "description": sch["description"],
                    "parameters": sch["parameters"],
                },
            }
        )
    return specs


def json_safe(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj
