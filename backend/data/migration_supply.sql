-- ── TEDARİK AJANI İÇİN YENİ ALANLAR VE TABLOLAR ─────────────────────────────
-- Yeni kurulum: backend/generate_data.py artık bu tabloları ve seed'i içerir.
-- Eski / eksik veritabanları için bu dosyayı bir kez çalıştırın:
--   psql -d kooperatif_db -f backend/data/migration_supply.sql

-- 1. urunler tablosuna tedarik süre alanı ekle
ALTER TABLE urunler
    ADD COLUMN IF NOT EXISTS lead_time_gun INT NOT NULL DEFAULT 7;

-- Kategori bazlı gerçekçi tedarik süreleri
UPDATE urunler SET lead_time_gun = 3
WHERE kategori IN ('Kahvaltılık') AND ad LIKE '%Bal%';

UPDATE urunler SET lead_time_gun = 5
WHERE kategori = 'Kahvaltılık' AND ad NOT LIKE '%Bal%';

UPDATE urunler SET lead_time_gun = 7
WHERE kategori IN ('Kişisel Bakım', 'Baharat');

UPDATE urunler SET lead_time_gun = 14
WHERE kategori IN ('Bakliyat', 'Zeytinyağı', 'Turşu');

UPDATE urunler SET lead_time_gun = 30
WHERE kategori = 'El Sanatı';

-- 2. Satış hareketleri tablosu (satış hızı hesabı için)
CREATE TABLE IF NOT EXISTS stok_hareketleri (
    id          SERIAL PRIMARY KEY,
    urun_id     INT REFERENCES urunler(id),
    miktar      INT NOT NULL,        -- pozitif: giriş, negatif: çıkış
    hareket_turu TEXT NOT NULL,      -- 'satis', 'iade', 'tedarik', 'duzeltme'
    aciklama    TEXT,
    olusturulma TIMESTAMP DEFAULT NOW()
);

-- 3. Bildirimler tablosu (proaktif uyarılar)
CREATE TABLE IF NOT EXISTS bildirimler (
    id              SERIAL PRIMARY KEY,
    tip             TEXT NOT NULL,          -- 'tedarik_onerisi', 'kritik_stok', 'transfer_onerisi'
    urun_id         INT REFERENCES urunler(id),
    kooperatif_id   INT REFERENCES kooperatifler(id),
    baslik          TEXT NOT NULL,
    govde           TEXT,                   -- mail taslağı veya uyarı metni
    kritiklik       TEXT NOT NULL,          -- 'critical', 'warning', 'info'
    veri            JSONB,                  -- ek analiz verisi (hız, gün, miktar vs)
    okundu          BOOLEAN DEFAULT FALSE,
    aksiyon_alindi  BOOLEAN DEFAULT FALSE,
    mail_dosya_yolu TEXT,                   -- mock mail dosya yolu
    olusturulma     TIMESTAMP DEFAULT NOW()
);

-- 4. Gerçekçi satış hareketi seed'i (satış hızı hesabı için)
-- Son 30 günde siparis_kalemleri'ndeki verileri stok_hareketleri'ne aktar
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
