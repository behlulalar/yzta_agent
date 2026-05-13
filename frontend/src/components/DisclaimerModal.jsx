import { useEffect } from "react";
import { X } from "lucide-react";
import "../styles/disclaimer-modal.css";

export default function DisclaimerModal({ isOpen, onClose }) {
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="disclaimer-modal-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="disclaimer-title"
    >
      <div className="disclaimer-modal" onClick={(e) => e.stopPropagation()}>
        <div className="disclaimer-modal-header">
          <h3 id="disclaimer-title">Yapay zekâ asistanı — bilgilendirme</h3>
          <button
            type="button"
            className="disclaimer-modal-close"
            onClick={onClose}
            aria-label="Kapat"
          >
            <X size={20} />
          </button>
        </div>
        <div className="disclaimer-modal-body">
          <p>
            Bu uygulama, kadın kooperatifleri ürün ve sipariş verilerinize dayalı demo bir asistanıdır.
            Yanıtlar otomatik üretilir; hata, eksik bilgi veya güncel olmayan içerik içerebilir.
          </p>
          <h4>Sorumluluk</h4>
          <ul>
            <li>
              Fiyat, stok, sipariş ve kargo bilgileri yalnızca bağlı veritabanındaki kayıtlara göredir;
              kesin işlem için resmi kanallarınızı kullanın.
            </li>
            <li>
              Yasal, muhasebe veya sözleşmesel konularda profesyonel danışmanlık yerine geçmez.
            </li>
          </ul>
          <h4>Veri ve gizlilik</h4>
          <p>
            Sohbet geçmişi tarayıcınızın yerel depolamasında tutulur; sunucuya yalnızca gönderdiğiniz mesaj
            metni gider (demo yapılandırmasına bağlıdır).
          </p>
        </div>
      </div>
    </div>
  );
}
