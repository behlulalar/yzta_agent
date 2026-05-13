import { useState } from "react";
import { login, saveToken } from "../services/api.js";
import "../styles/login-page.css";

export default function LoginPage({ onLoginSuccess, onCancel }) {
  const [koopAdi, setKoopAdi] = useState("");
  const [sifre, setSifre] = useState("");
  const [loading, setLoading] = useState(false);
  const [hata, setHata] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    setHata("");
    setLoading(true);
    try {
      const data = await login(koopAdi.trim(), sifre);
      saveToken(data.access_token, {
        id: data.kooperatif_id,
        adi: data.kooperatif_adi,
      });
      setSifre("");
      onLoginSuccess?.();
    } catch (err) {
      setHata(err?.message || "Giriş başarısız");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page-root">
      <div className="login-card">
        <h1 className="login-brand">🌿 Kadın Kooperatifleri</h1>
        <p className="login-subtitle">Kooperatif Yönetim Paneli</p>

        <form onSubmit={handleSubmit}>
          <div className="login-field">
            <label htmlFor="koop-adi">Kooperatif adı</label>
            <input
              id="koop-adi"
              type="text"
              autoComplete="username"
              placeholder="ör. Zap"
              value={koopAdi}
              onChange={(e) => setKoopAdi(e.target.value)}
              disabled={loading}
              required
            />
          </div>
          <div className="login-field">
            <label htmlFor="koop-sifre">Şifre</label>
            <input
              id="koop-sifre"
              type="password"
              autoComplete="current-password"
              value={sifre}
              onChange={(e) => setSifre(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          <p className="login-error" role="alert">
            {hata}
          </p>

          <button type="submit" className="login-submit" disabled={loading}>
            {loading ? "Giriş yapılıyor..." : "Giriş Yap"}
          </button>
        </form>

        {onCancel ? (
          <button type="button" className="login-back" onClick={onCancel}>
            Müşteri olarak devam et
          </button>
        ) : null}
      </div>
    </div>
  );
}
