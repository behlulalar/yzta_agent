"""
Kadın Kooperatifleri - Sentetik Veri Üretici
Çalıştırmadan önce: pip install -r requirements.txt (veya faker psycopg2-binary python-dotenv)
PostgreSQL bağlantısı için .env dosyası oluştur:
  DB_HOST=localhost
  DB_PORT=5432
  DB_NAME=kooperatif_db
  DB_USER=postgres
  DB_PASSWORD=yourpassword
"""

import random
import os
from datetime import datetime, timedelta
from faker import Faker
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()
fake = Faker("tr_TR")
random.seed(42)

# ── GERÇEK VERİ (siteden alındı) ──────────────────────────────────────────────

KOOPERATIFLER = [
    {"id": 1,  "ad": "Zap Kadın Kooperatifi",              "bolge": "Hakkari"},
    {"id": 2,  "ad": "Çiçeklerin Özü Kadın Kooperatifi",   "bolge": "Hakkari"},
    {"id": 3,  "ad": "Demeter Kadın Kooperatifi",           "bolge": "Hakkari"},
    {"id": 4,  "ad": "Sinanpaşa Kadın Kooperatifi",         "bolge": "Afyonkarahisar"},
    {"id": 5,  "ad": "Başmakçı Kadın Kooperatifi",          "bolge": "Afyonkarahisar"},
    {"id": 6,  "ad": "Emirdağ Kadın Kooperatifi",           "bolge": "Afyonkarahisar"},
    {"id": 7,  "ad": "Aizanoi Kadın Kooperatifi",           "bolge": "Kütahya"},
    {"id": 8,  "ad": "Domaniç Hanımeli Kadın Kooperatifi",  "bolge": "Kütahya"},
    {"id": 9,  "ad": "Katılımcı Kadınlar Kooperatifi",      "bolge": "Kütahya"},
    {"id": 10, "ad": "Ortakbahçe Kadın Kooperatifi",        "bolge": "Giresun"},
    {"id": 11, "ad": "Çal Kadın Kooperatifi",               "bolge": "Denizli"},
    {"id": 12, "ad": "Rihen Kadın Kooperatifi",             "bolge": "Hatay"},
    {"id": 13, "ad": "Horanta Kadın Kooperatifi",           "bolge": "Hatay"},
    {"id": 14, "ad": "Tokat Kadın Kooperatifi",             "bolge": "Tokat"},
    {"id": 15, "ad": "Develi Kadın Kooperatifi",            "bolge": "Kayseri"},
    {"id": 16, "ad": "Ayvalık Tarımsal Kalkınma Kooperatifi","bolge": "Balıkesir"},
    {"id": 17, "ad": "Atmalı Kadın Kooperatifi",            "bolge": "Adıyaman"},
    {"id": 18, "ad": "Biga Kadın Kooperatifi",              "bolge": "Çanakkale"},
    {"id": 19, "ad": "Eşme Kadın Kooperatifi",              "bolge": "Uşak"},
    {"id": 20, "ad": "Trakyam Kadın Kooperatifi",           "bolge": "Tekirdağ"},
]

# (ürün_adı, kategori, birim, fiyat_aralığı, kooperatif_id_listesi)
URUN_SABLONLARI = [
    # GIDA - Kahvaltılık
    ("Süzme Bal",               "Kahvaltılık",  "kavanoz", (250, 700),  [1, 2, 3]),
    ("Çiçek Balı",              "Kahvaltılık",  "kavanoz", (200, 550),  [2, 5, 6]),
    ("Reçel - Kuşburnu",        "Kahvaltılık",  "kavanoz", (180, 350),  [14, 11]),
    ("Reçel - İncir",           "Kahvaltılık",  "kavanoz", (160, 320),  [13, 12]),
    ("Tahin",                   "Kahvaltılık",  "kg",      (245, 820),  [1, 3, 5]),
    ("Tahin-Pekmez",            "Kahvaltılık",  "kavanoz", (180, 400),  [4, 7]),
    ("Fındık Ezmesi",           "Kahvaltılık",  "kavanoz", (300, 600),  [10]),
    ("Domates Salçası",         "Kahvaltılık",  "kg",      (240, 425),  [12, 13, 14]),
    ("Biber Salçası",           "Kahvaltılık",  "kg",      (250, 400),  [13, 17]),
    ("Karışık Salça",           "Kahvaltılık",  "kg",      (280, 450),  [14]),
    # GIDA - Tahıl & Bakliyat
    ("Ev Eriştesi - Sade",      "Bakliyat",     "kg",      (110, 295),  [4, 8, 9, 19]),
    ("Ev Eriştesi - Sebzeli",   "Bakliyat",     "kg",      (135, 344),  [4, 9, 19]),
    ("Ev Eriştesi - Pancarlı",  "Bakliyat",     "kg",      (135, 334),  [4, 19]),
    ("Tarhana",                 "Bakliyat",     "kg",      (200, 325),  [8, 18, 14]),
    ("Kuru Fasulye",            "Bakliyat",     "kg",      (145, 220),  [6]),
    ("Nohut",                   "Bakliyat",     "kg",      (130, 200),  [6]),
    ("Pirinç",                  "Bakliyat",     "kg",      (200, 350),  [1]),
    ("Yufka",                   "Bakliyat",     "paket",   (130, 220),  [9]),
    ("Ev Mantısı",              "Bakliyat",     "paket",   (350, 550),  [15]),
    ("Bulgur",                  "Bakliyat",     "kg",      (90, 180),   [4, 5]),
    # GIDA - Zeytinyağı & Zeytin
    ("Naturel Sızma Zeytinyağı","Zeytinyağı",   "litre",   (400, 700),  [13, 16]),
    ("Sızma Zeytinyağı",        "Zeytinyağı",   "litre",   (350, 600),  [16]),
    ("Yeşil Zeytin Salamurası", "Zeytinyağı",   "kg",      (200, 400),  [13]),
    # GIDA - Turşu & Salamura
    ("Pancar Sapı Turşusu",     "Turşu",        "kg",      (175, 575),  [7]),
    ("Karışık Turşu",           "Turşu",        "kg",      (150, 300),  [14, 11]),
    ("Salamura Asma Yaprağı",   "Turşu",        "paket",   (100, 200),  [20]),
    # GIDA - Baharat & Aktar
    ("Dağ Kekiği",              "Baharat",      "paket",   (150, 325),  [2, 13]),
    ("Kuşburnu",                "Baharat",      "paket",   (120, 250),  [2, 14]),
    ("Lavanta Çiçeği",          "Baharat",      "paket",   (200, 420),  [11]),
    ("Nar Ekşisi",              "Baharat",      "şişe",    (200, 700),  [13, 17]),
    ("Fermente Sirke",          "Baharat",      "şişe",    (150, 350),  [11, 16]),
    # KİŞİSEL BAKIM
    ("Defne Sabunu",            "Kişisel Bakım","adet",    (195, 280),  [12, 13]),
    ("Doğal El Sabunu",         "Kişisel Bakım","adet",    (100, 250),  [7, 8]),
    ("Kozmetik Şap Taşı",       "Kişisel Bakım","adet",    (150, 250),  [9]),
    ("Bitkisel Aromatik Yağ",   "Kişisel Bakım","şişe",    (200, 500),  [2, 3]),
    # EL SANATI & TEKSTİL
    ("Lavanta Kesesi - El Nakışlı","El Sanatı", "adet",    (145, 450),  [7, 9]),
    ("Keçe Broş",               "El Sanatı",    "adet",    (400, 700),  [6]),
    ("Kilim & Seccade",         "El Sanatı",    "adet",    (800, 3000), [4, 20]),
    ("Örgü Çanta",              "El Sanatı",    "adet",    (300, 900),  [8, 15]),
    ("Amigurumi Oyuncak",       "El Sanatı",    "adet",    (150, 450),  [9, 18]),
]

# ── YARDIMCI FONKSİYONLAR ─────────────────────────────────────────────────────

def rastgele_fiyat(aralik):
    taban, tavan = aralik
    fiyat = random.randint(taban // 10, tavan // 10) * 10
    return max(taban, min(fiyat, tavan))

def rastgele_stok():
    """Gerçekçi stok dağılımı: bazı ürünler kritik seviyede"""
    agirliklar = [5, 15, 40, 30, 10]   # kritik, az, normal, bol, çok bol
    seviyeler  = [
        random.randint(1, 4),
        random.randint(5, 14),
        random.randint(15, 49),
        random.randint(50, 99),
        random.randint(100, 200),
    ]
    return random.choices(seviyeler, weights=agirliklar, k=1)[0]

def kritik_esik(stok):
    return max(5, int(stok * 0.2))

# ── VERİTABANI KURULUMU ───────────────────────────────────────────────────────

SCHEMA_SQL = """
DROP TABLE IF EXISTS siparis_kalemleri CASCADE;
DROP TABLE IF EXISTS siparisler CASCADE;
DROP TABLE IF EXISTS bildirimler CASCADE;
DROP TABLE IF EXISTS stok_hareketleri CASCADE;
DROP TABLE IF EXISTS urunler CASCADE;
DROP TABLE IF EXISTS muhasebe CASCADE;
DROP TABLE IF EXISTS musteriler CASCADE;
DROP TABLE IF EXISTS kooperatifler CASCADE;

CREATE TABLE kooperatifler (
    id          SERIAL PRIMARY KEY,
    ad          TEXT NOT NULL,
    bolge       TEXT NOT NULL,
    telefon     TEXT,
    email       TEXT,
    sifre_hash  TEXT,
    aktif       BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE urunler (
    id              SERIAL PRIMARY KEY,
    kooperatif_id   INT REFERENCES kooperatifler(id),
    ad              TEXT NOT NULL,
    kategori        TEXT NOT NULL,
    birim           TEXT NOT NULL,
    fiyat           NUMERIC(10,2) NOT NULL,
    stok            INT NOT NULL DEFAULT 0,
    kritik_esik     INT NOT NULL DEFAULT 5,
    lead_time_gun   INT NOT NULL DEFAULT 7,
    aciklama        TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE musteriler (
    id          SERIAL PRIMARY KEY,
    ad_soyad    TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    telefon     TEXT,
    sehir       TEXT,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE siparisler (
    id              SERIAL PRIMARY KEY,
    musteri_id      INT REFERENCES musteriler(id),
    toplam_tutar    NUMERIC(10,2) NOT NULL,
    durum           TEXT NOT NULL DEFAULT 'beklemede',
    kargo_firma     TEXT,
    kargo_takip_no  TEXT,
    kargo_durum     TEXT DEFAULT 'hazırlanıyor',
    adres           TEXT,
    notlar          TEXT,
    olusturulma     TIMESTAMP NOT NULL,
    guncelleme      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE siparis_kalemleri (
    id          SERIAL PRIMARY KEY,
    siparis_id  INT REFERENCES siparisler(id),
    urun_id     INT REFERENCES urunler(id),
    miktar      INT NOT NULL,
    birim_fiyat NUMERIC(10,2) NOT NULL
);

CREATE TABLE muhasebe (
    id          SERIAL PRIMARY KEY,
    tarih       DATE NOT NULL,
    tur         TEXT NOT NULL,   -- 'gelir' veya 'gider'
    kategori    TEXT NOT NULL,
    tutar       NUMERIC(10,2) NOT NULL,
    aciklama    TEXT,
    kooperatif_id INT REFERENCES kooperatifler(id)
);

CREATE TABLE stok_hareketleri (
    id          SERIAL PRIMARY KEY,
    urun_id     INT REFERENCES urunler(id),
    miktar      INT NOT NULL,
    hareket_turu TEXT NOT NULL,
    aciklama    TEXT,
    olusturulma TIMESTAMP DEFAULT NOW()
);

CREATE TABLE bildirimler (
    id              SERIAL PRIMARY KEY,
    tip             TEXT NOT NULL,
    urun_id         INT REFERENCES urunler(id),
    kooperatif_id   INT REFERENCES kooperatifler(id),
    baslik          TEXT NOT NULL,
    govde           TEXT,
    kritiklik       TEXT NOT NULL,
    veri            JSONB,
    okundu          BOOLEAN DEFAULT FALSE,
    aksiyon_alindi  BOOLEAN DEFAULT FALSE,
    mail_dosya_yolu TEXT,
    olusturulma     TIMESTAMP DEFAULT NOW()
);
"""

# Sipariş kalemlerinden son 30 gün satış hareketleri (tekrar çalıştırmada çift yazmaz)
SEED_STOK_HAREKET_SQL = """
INSERT INTO stok_hareketleri (urun_id, miktar, hareket_turu, aciklama, olusturulma)
SELECT
    sk.urun_id,
    -sk.miktar,
    'satis',
    'Sipariş #' || s.id::text || ' kalem #' || sk.id::text || ' otomatik aktarım',
    s.olusturulma
FROM siparis_kalemleri sk
JOIN siparisler s ON s.id = sk.siparis_id
WHERE s.olusturulma >= NOW() - INTERVAL '30 days'
  AND s.durum != 'iptal'
  AND NOT EXISTS (
    SELECT 1 FROM stok_hareketleri sh
    WHERE sh.aciklama = 'Sipariş #' || s.id::text || ' kalem #' || sk.id::text || ' otomatik aktarım'
      AND sh.hareket_turu = 'satis'
  );
"""

LEAD_TIME_CATEGORY_SQL = [
    """
    UPDATE urunler SET lead_time_gun = 3
    WHERE kategori IN ('Kahvaltılık') AND ad LIKE '%Bal%';
    """,
    """
    UPDATE urunler SET lead_time_gun = 5
    WHERE kategori = 'Kahvaltılık' AND ad NOT LIKE '%Bal%';
    """,
    """
    UPDATE urunler SET lead_time_gun = 7
    WHERE kategori IN ('Kişisel Bakım', 'Baharat');
    """,
    """
    UPDATE urunler SET lead_time_gun = 14
    WHERE kategori IN ('Bakliyat', 'Zeytinyağı', 'Turşu');
    """,
    """
    UPDATE urunler SET lead_time_gun = 30
    WHERE kategori = 'El Sanatı';
    """,
]

# ── VERİ ÜRETME FONKSİYONLARI ─────────────────────────────────────────────────

def uretici_urunler():
    urunler = []
    urun_id = 1
    for sablon in URUN_SABLONLARI:
        ad, kategori, birim, fiyat_aralik, koop_idler = sablon
        for koop_id in koop_idler:
            stok = rastgele_stok()
            fiyat = rastgele_fiyat(fiyat_aralik)
            koop = next(k for k in KOOPERATIFLER if k["id"] == koop_id)
            aciklama = (
                f"{koop['bolge']} yöresinden, {koop['ad']} tarafından üretilen "
                f"doğal ve el yapımı {ad.lower()}. "
                f"Katkısız, geleneksel yöntemlerle hazırlanmıştır."
            )
            urunler.append({
                "id": urun_id,
                "kooperatif_id": koop_id,
                "ad": ad,
                "kategori": kategori,
                "birim": birim,
                "fiyat": fiyat,
                "stok": stok,
                "kritik_esik": kritik_esik(stok),
                "aciklama": aciklama,
            })
            urun_id += 1
    return urunler

def uretici_musteriler(n=150):
    musteriler = []
    sehirler = [
        "İstanbul", "Ankara", "İzmir", "Bursa", "Antalya",
        "Konya", "Adana", "Gaziantep", "Mersin", "Kayseri",
        "Sakarya", "Eskişehir", "Trabzon", "Samsun", "Denizli"
    ]
    for i in range(1, n + 1):
        musteriler.append({
            "id": i,
            "ad_soyad": fake.name(),
            "email": fake.unique.email(),
            "telefon": fake.phone_number(),
            "sehir": random.choice(sehirler),
        })
    return musteriler

def uretici_siparisler(urunler, musteriler, n=400):
    kargo_firmalar = ["Yurtiçi Kargo", "Aras Kargo", "MNG Kargo", "PTT Kargo", "Sürat Kargo"]
    durumlar = ["teslim edildi", "teslim edildi", "teslim edildi", "kargoda", "hazırlanıyor", "iptal"]
    kargo_durumlar = {
        "teslim edildi": "teslim edildi",
        "kargoda": random.choice(["yolda", "dağıtımda", "şubede bekliyor"]),
        "hazırlanıyor": "hazırlanıyor",
        "iptal": "iptal",
    }

    siparisler = []
    kalemler = []
    kalem_id = 1

    bitis = datetime.now()
    baslangic = bitis - timedelta(days=90)

    for i in range(1, n + 1):
        musteri = random.choice(musteriler)
        siparis_tarihi = fake.date_time_between(start_date=baslangic, end_date=bitis)
        durum = random.choices(
            ["teslim edildi", "kargoda", "hazırlanıyor", "iptal"],
            weights=[60, 20, 15, 5], k=1
        )[0]

        urun_sayisi = random.randint(1, 4)
        secilen_urunler = random.sample(urunler, min(urun_sayisi, len(urunler)))
        toplam = 0
        siparis_kalemleri_gecici = []

        for urun in secilen_urunler:
            miktar = random.randint(1, 3)
            birim_fiyat = float(urun["fiyat"]) * random.uniform(0.95, 1.05)
            birim_fiyat = round(birim_fiyat, 2)
            toplam += miktar * birim_fiyat
            siparis_kalemleri_gecici.append({
                "id": kalem_id,
                "siparis_id": i,
                "urun_id": urun["id"],
                "miktar": miktar,
                "birim_fiyat": birim_fiyat,
            })
            kalem_id += 1

        kargo_firma = random.choice(kargo_firmalar) if durum != "hazırlanıyor" else None
        takip_no = f"TR{random.randint(10**9, 10**10-1)}" if kargo_firma else None

        siparisler.append({
            "id": i,
            "musteri_id": musteri["id"],
            "toplam_tutar": round(toplam, 2),
            "durum": durum,
            "kargo_firma": kargo_firma,
            "kargo_takip_no": takip_no,
            "kargo_durum": kargo_durumlar.get(durum, "hazırlanıyor"),
            "adres": fake.address().replace("\n", ", "),
            "notlar": random.choice(["", "", "", "Lütfen akşam teslim edin.", "Kapıya bırakabilirsiniz."]),
            "olusturulma": siparis_tarihi,
        })
        kalemler.extend(siparis_kalemleri_gecici)

    return siparisler, kalemler

def uretici_muhasebe(siparisler, urunler):
    """Sipariş gelirlerinden + rastgele giderlerden muhasebe oluştur"""
    kayitlar = []
    kayit_id = 1

    # Gelirler: teslim edilen siparişlerden
    for siparis in siparisler:
        if siparis["durum"] == "teslim edildi":
            tarih = siparis["olusturulma"].date()
            kayitlar.append({
                "id": kayit_id,
                "tarih": tarih,
                "tur": "gelir",
                "kategori": "Satış",
                "tutar": siparis["toplam_tutar"],
                "aciklama": f"Sipariş #{siparis['id']} - Satış geliri",
                "kooperatif_id": None,
            })
            kayit_id += 1

    # Giderler: kira, nakliye, ambalaj vb.
    gider_kategorileri = [
        ("Kira", 2000, 4000),
        ("Nakliye", 500, 2000),
        ("Ambalaj Malzemesi", 200, 800),
        ("Elektrik-Su", 300, 700),
        ("Personel", 3000, 8000),
        ("Pazarlama", 500, 2000),
    ]

    bitis = datetime.now()
    baslangic = bitis - timedelta(days=90)
    tarih = baslangic.date()

    while tarih <= bitis.date():
        if tarih.day == 1:  # Her ayın başında sabit giderler
            for kategori, taban, tavan in gider_kategorileri[:4]:
                koop = random.choice(KOOPERATIFLER)
                kayitlar.append({
                    "id": kayit_id,
                    "tarih": tarih,
                    "tur": "gider",
                    "kategori": kategori,
                    "tutar": random.randint(taban, tavan),
                    "aciklama": f"{kategori} - Aylık gider",
                    "kooperatif_id": koop["id"],
                })
                kayit_id += 1
        tarih += timedelta(days=1)

    return kayitlar

# ── VERİTABANINA YAZMA ────────────────────────────────────────────────────────

def db_baglan():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "kooperatif_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )

def db_yukle(conn, urunler, musteriler, siparisler, kalemler, muhasebe_kayitlari):
    cur = conn.cursor()

    print("📦 Schema oluşturuluyor...")
    cur.execute(SCHEMA_SQL)

    print("🏪 Kooperatifler yazılıyor...")
    execute_values(cur,
        "INSERT INTO kooperatifler (id, ad, bolge, telefon, email) VALUES %s",
        [(k["id"], k["ad"], k["bolge"],
          fake.phone_number(), fake.email()) for k in KOOPERATIFLER]
    )

    print("🛍️  Ürünler yazılıyor...")
    execute_values(cur,
        """INSERT INTO urunler
           (id, kooperatif_id, ad, kategori, birim, fiyat, stok, kritik_esik, aciklama)
           VALUES %s""",
        [(u["id"], u["kooperatif_id"], u["ad"], u["kategori"],
          u["birim"], u["fiyat"], u["stok"], u["kritik_esik"], u["aciklama"])
         for u in urunler]
    )

    print("⏱️  Tedarik süreleri (lead_time_gun) güncelleniyor...")
    for stmt in LEAD_TIME_CATEGORY_SQL:
        cur.execute(stmt)

    print("👤 Müşteriler yazılıyor...")
    execute_values(cur,
        "INSERT INTO musteriler (id, ad_soyad, email, telefon, sehir) VALUES %s",
        [(m["id"], m["ad_soyad"], m["email"], m["telefon"], m["sehir"])
         for m in musteriler]
    )

    print("📋 Siparişler yazılıyor...")
    execute_values(cur,
        """INSERT INTO siparisler
           (id, musteri_id, toplam_tutar, durum, kargo_firma,
            kargo_takip_no, kargo_durum, adres, notlar, olusturulma)
           VALUES %s""",
        [(s["id"], s["musteri_id"], s["toplam_tutar"], s["durum"],
          s["kargo_firma"], s["kargo_takip_no"], s["kargo_durum"],
          s["adres"], s["notlar"], s["olusturulma"])
         for s in siparisler]
    )

    print("🧾 Sipariş kalemleri yazılıyor...")
    execute_values(cur,
        """INSERT INTO siparis_kalemleri
           (id, siparis_id, urun_id, miktar, birim_fiyat) VALUES %s""",
        [(k["id"], k["siparis_id"], k["urun_id"],
          k["miktar"], k["birim_fiyat"]) for k in kalemler]
    )

    print("💰 Muhasebe yazılıyor...")
    execute_values(cur,
        """INSERT INTO muhasebe
           (id, tarih, tur, kategori, tutar, aciklama, kooperatif_id) VALUES %s""",
        [(m["id"], m["tarih"], m["tur"], m["kategori"],
          m["tutar"], m["aciklama"], m["kooperatif_id"])
         for m in muhasebe_kayitlari]
    )

    print("📊 Stok hareketleri (son 30 gün satışları) aktarılıyor...")
    cur.execute(SEED_STOK_HAREKET_SQL)

    # Sequence'leri güncelle (boş tabloda COALESCE ile güvenli)
    for tablo, alan in [
        ("kooperatifler", "kooperatifler_id_seq"),
        ("urunler", "urunler_id_seq"),
        ("musteriler", "musteriler_id_seq"),
        ("siparisler", "siparisler_id_seq"),
        ("siparis_kalemleri", "siparis_kalemleri_id_seq"),
        ("muhasebe", "muhasebe_id_seq"),
        ("stok_hareketleri", "stok_hareketleri_id_seq"),
        ("bildirimler", "bildirimler_id_seq"),
    ]:
        cur.execute(
            f"SELECT setval('{alan}', COALESCE((SELECT MAX(id) FROM {tablo}), 1))"
        )

    conn.commit()
    cur.close()

# ── ANA AKIŞ ──────────────────────────────────────────────────────────────────

def main():
    print("🚀 Sentetik veri üretimi başlıyor...\n")

    urunler          = uretici_urunler()
    musteriler       = uretici_musteriler(150)
    siparisler, kalemler = uretici_siparisler(urunler, musteriler, 400)
    muhasebe_kayitlari   = uretici_muhasebe(siparisler, urunler)

    print(f"✅ {len(KOOPERATIFLER)} kooperatif")
    print(f"✅ {len(urunler)} ürün")
    print(f"✅ {len(musteriler)} müşteri")
    print(f"✅ {len(siparisler)} sipariş / {len(kalemler)} kalem")
    print(f"✅ {len(muhasebe_kayitlari)} muhasebe kaydı\n")

    print("🔌 Veritabanına bağlanılıyor...")
    conn = db_baglan()
    db_yukle(conn, urunler, musteriler, siparisler, kalemler, muhasebe_kayitlari)
    conn.close()

    print("\n🎉 Tamamlandı! Veritabanı hazır.")

if __name__ == "__main__":
    main()