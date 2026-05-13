import { useEffect, useRef, useState } from "react";
import {
  Menu,
  Send,
  Hourglass,
  Hand,
  Trophy,
  Package,
  Truck,
  BarChart2,
} from "lucide-react";
import Sidebar from "./Sidebar.jsx";
import ChatMessageRow from "./ChatMessageRow.jsx";
import DisclaimerModal from "./DisclaimerModal.jsx";
import { api, SellerAuthRequiredError, getKooperatif, isLoggedIn } from "../services/api.js";
import {
  generateSessionId,
  generateSessionTitle,
  getSession,
  saveSession,
} from "../utils/sessionStorage.js";
import "../styles/chat-layout.css";

function formatChatError(err) {
  const raw = err?.message || String(err);
  if (/429|quota|kota|Too Many Requests/i.test(raw)) {
    return "İstek kotası veya hız sınırına takıldık. Lütfen kısa süre sonra tekrar deneyin.";
  }
  if (/Failed to fetch|NetworkError|ERR_NETWORK/i.test(raw)) {
    return "Sunucuya bağlanılamıyor. Backend çalışıyor mu kontrol edin.";
  }
  return raw.replace(/^API \d+:\s*/, "").slice(0, 500) || "Bir hata oluştu.";
}

export default function ChatInterface({
  mode = "musteri",
  onModeChange = () => {},
  onSellerUnauthorized,
  onSellerLogout,
}) {
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [sessionListVersion, setSessionListVersion] = useState(0);
  const [disclaimerOpen, setDisclaimerOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  useEffect(() => {
    if (mode !== "satici" || !isLoggedIn()) {
      setNotifications([]);
      setNotifOpen(false);
      return;
    }

    const fetchNotifs = async () => {
      try {
        const res = await api.notifications();
        setNotifications(res.items || []);
      } catch (_) {}
    };

    fetchNotifs();
    const interval = setInterval(fetchNotifs, 10000);
    return () => clearInterval(interval);
  }, [mode]);

  const handleNotifAction = async (id) => {
  try {
    await api.notificationAction(id);
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  } catch (err) {
    console.error("Bildirim onaylanamadı:", err);
  }
};

  const switchMode = (newMode) => {
    if (newMode === mode) return;
    setMessages((prev) => [
      ...prev,
      {
        role: "system",
        content: `Mod: ${newMode === "musteri" ? "Müşteri" : "Satıcı"}`,
        timestamp: new Date(),
      },
    ]);
    onModeChange(newMode);
  };

  const startNewChat = () => {
    setMessages([]);
    setCurrentSessionId(null);
    setInputValue("");
  };

  const loadSession = (sessionId) => {
    const session = getSession(sessionId);
    if (!session) return;
    let sessionMode = session.mode === "satici" ? "satici" : "musteri";
    if (sessionMode === "satici" && !isLoggedIn()) {
      sessionMode = "musteri";
    }
    onModeChange(sessionMode);
    setMessages(session.messages || []);
    setCurrentSessionId(session.id);
    setSidebarOpen(false);
  };

  const handleSendMessage = async () => {
    const text = inputValue.trim();
    if (!text || isLoading) return;

    let sessionId = currentSessionId;
    if (!sessionId) {
      sessionId = generateSessionId();
      setCurrentSessionId(sessionId);
    }

    const userMessage = {
      role: "user",
      content: text,
      timestamp: new Date(),
    };

    const newMessages = [...messages, userMessage];
    setMessages(newMessages);
    setInputValue("");
    setIsLoading(true);

    try {
      const transcript = newMessages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({ role: m.role, content: m.content }));
      const prevSession = getSession(sessionId);
      const orderCtx = {};
      if (prevSession?.order_step && prevSession.order_step !== "idle") {
        orderCtx.order_step = prevSession.order_step;
      }
      if (
        prevSession?.order_draft &&
        typeof prevSession.order_draft === "object" &&
        Object.keys(prevSession.order_draft).length > 0
      ) {
        orderCtx.order_draft = prevSession.order_draft;
      }
      const res = await api.chat(transcript, mode, orderCtx);
      const assistantMessage = {
        role: "assistant",
        content: res.reply,
        timestamp: new Date(),
      };
      const finalMessages = [...newMessages, assistantMessage];
      setMessages(finalMessages);

      const prev = getSession(sessionId);
      const session = {
        id: sessionId,
        title:
          messages.filter((m) => m.role === "user").length === 0
            ? generateSessionTitle(text)
            : prev?.title || generateSessionTitle(text),
        messages: finalMessages,
        mode,
        order_step: res.order_step ?? "idle",
        order_draft:
          res.order_draft && typeof res.order_draft === "object"
            ? res.order_draft
            : {},
        createdAt: prev?.createdAt || new Date(),
        updatedAt: new Date(),
      };
      saveSession(session);
      setSessionListVersion((v) => v + 1);
    } catch (err) {
      console.error(err);
      if (err instanceof SellerAuthRequiredError) {
        onSellerUnauthorized?.();
        return;
      }
      const assistantMessage = {
        role: "assistant",
        content: `⚠️ ${formatChatError(err)}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, assistantMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const examples =
    mode === "musteri"
      ? [
          {
            icon: <Trophy size={20} />,
            text: "Hangi ürün kategorileriniz var?",
          },
          {
            icon: <Package size={20} />,
            text: "Bal ürünlerinden önerir misin?",
          },
          {
            icon: <Truck size={20} />,
            text: "12 numaralı siparişimin kargosu nerede?",
          },
        ]
      : [
          {
            icon: <BarChart2 size={20} />,
            text: "Bugünün satış özetini göster.",
          },
          {
            icon: <Package size={20} />,
            text: "Kritik stoktaki ürünleri listele.",
          },
          {
            icon: <Trophy size={20} />,
            text: "Son 10 siparişi göster.",
          },
        ];

  return (
    <div className="chat-container">
      {sidebarOpen && (
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarOpen(false)}
          onKeyDown={(e) => e.key === "Escape" && setSidebarOpen(false)}
          role="button"
          tabIndex={0}
          aria-label="Kenar çubuğunu kapat"
        />
      )}

      <Sidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(false)}
        onStartNewChat={() => {
          startNewChat();
          setSessionListVersion((v) => v + 1);
        }}
        onLoadSession={loadSession}
        currentSessionId={currentSessionId}
        refreshVersion={sessionListVersion}
        onSessionsMutated={() => setSessionListVersion((v) => v + 1)}
      />

      <div className={`main-content ${!sidebarOpen ? "sidebar-closed" : ""}`}>
        <header className="chat-header">
          <div className="chat-header-row">
            <div className="header-content header-main-block">
              <button
                type="button"
                className="sidebar-toggle"
                onClick={() => setSidebarOpen(!sidebarOpen)}
                aria-label="Sohbetler menüsü"
              >
                <Menu size={24} />
              </button>
              <div className="header-title">
                <h1>
                  <span className="app-header-brand" aria-hidden>
                    🌸
                  </span>
                  Kadın Kooperatifleri AI Asistanı
                </h1>
                <p>Ürün keşfi, sipariş ve stok — müşteri veya satıcı modunda</p>
              </div>
            </div>
<div className="header-right">
  <div className="mode-switch" role="group" aria-label="Konuşma modu">
    <button
      type="button"
      className={mode === "musteri" ? "active" : ""}
      onClick={() => switchMode("musteri")}
    >
      Müşteri
    </button>
    <button
      type="button"
      className={mode === "satici" ? "active" : ""}
      onClick={() => switchMode("satici")}
    >
      Satıcı
    </button>
  </div>

  {mode === "satici" && isLoggedIn() && (
    <button
      type="button"
      className="seller-logout-btn"
      onClick={() => onSellerLogout?.()}
    >
      Çıkış
    </button>
  )}

  {mode === "satici" && isLoggedIn() && (
    <div className="notif-wrapper">
      <button
        type="button"
        className="notif-btn"
        onClick={() => setNotifOpen(!notifOpen)}
        aria-label="Bildirimler"
      >
        🔔
        {notifications.length > 0 && (
          <span className="notif-badge">{notifications.length}</span>
        )}
      </button>
      {notifOpen && (
        <div className="notif-panel">
          <div className="notif-panel-header">
            <strong>Tedarik Uyarıları</strong>
            <span className="notif-count">{notifications.length} yeni</span>
          </div>
          {notifications.length === 0 ? (
            <div className="notif-empty">Yeni bildirim yok 🎉</div>
          ) : (
            notifications.map((n) => (
              <div key={n.id} className={`notif-item ${n.kritiklik}`}>
                <p className="notif-title">{n.baslik}</p>
                <p className="notif-body">{(n.govde || "").slice(0, 120)}...</p>
                <button
                  type="button"
                  className="notif-action-btn"
                  onClick={() => handleNotifAction(n.id)}
                >
                  ✅ Onayla ve Gönder
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )}
</div>
          </div>
        </header>

        {mode === "satici" && getKooperatif()?.adi ? (
          <div className="seller-coop-bar">
            🏪 {getKooperatif().adi} olarak giriş yapıldı
          </div>
        ) : null}

        <div className="messages-container">
          {messages.length === 0 ? (
            <div className="welcome-screen">
              <div className="welcome-content">
                <h2>
                  <Hand size={28} /> Hoş geldiniz
                </h2>
                <p>
                  Kadın kooperatifleri ürünleri hakkında sorun; sipariş veya stok için yardım al.
                  Modunu üstten seçebilirsin.
                </p>
                <div className="example-questions">
                  <h3>Örnek sorular</h3>
                  {examples.map((ex) => (
                    <button
                      key={ex.text}
                      type="button"
                      className="example-card"
                      onClick={() => setInputValue(ex.text)}
                    >
                      <span className="example-icon">{ex.icon}</span>
                      <span>{ex.text}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <>
              {messages.map((msg, index) =>
                msg.role === "system" ? (
                  <div key={index} className="system-banner-row">
                    <span className="system-banner">{msg.content}</span>
                  </div>
                ) : (
                  <ChatMessageRow key={index} message={msg} />
                ),
              )}
              {isLoading && (
                <div className="loading-indicator">
                  <div className="typing-dots" aria-hidden>
                    <span />
                    <span />
                    <span />
                  </div>
                  <p>Düşünüyorum...</p>
                </div>
              )}
            </>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-container">
          <div className="input-wrapper">
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Mesajınızı yazın..."
              disabled={isLoading}
              rows={1}
            />
            <button
              type="button"
              onClick={handleSendMessage}
              disabled={isLoading || !inputValue.trim()}
              className="send-button"
              aria-label="Gönder"
            >
              {isLoading ? <Hourglass size={20} /> : <Send size={20} />}
            </button>
          </div>
          <div className="input-hint">
            <small className="input-hint-desktop">
              Enter ile gönder • Shift+Enter ile yeni satır
            </small>
            <small className="ai-disclaimer">
              Yanıtlar yapay zekâ ile üretilir; hata yapabilir.{" "}
              <button
                type="button"
                className="ai-disclaimer-link"
                onClick={() => setDisclaimerOpen(true)}
              >
                Ayrıntılar
              </button>
            </small>
          </div>
        </div>

        <DisclaimerModal isOpen={disclaimerOpen} onClose={() => setDisclaimerOpen(false)} />
      </div>
    </div>
  );
}