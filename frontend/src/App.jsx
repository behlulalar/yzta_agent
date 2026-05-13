import { useEffect, useState } from "react";
import ChatInterface from "./components/ChatInterface.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import {
  isLoggedIn as sellerHasStoredSession,
  logout as apiLogout,
} from "./services/api.js";

export default function App() {
  const [sellerLoggedIn, setSellerLoggedIn] = useState(false);
  const [view, setView] = useState("musteri");

  useEffect(() => {
    if (sellerHasStoredSession()) {
      setSellerLoggedIn(true);
    }
  }, []);

  const handleLoginSuccess = () => {
    setSellerLoggedIn(true);
  };

  const handleLogout = async () => {
    await apiLogout();
    setSellerLoggedIn(false);
    setView("musteri");
  };

  const handleModeChange = (next) => {
    if (next === "musteri") {
      setView("musteri");
      return;
    }
    setView("satici");
    if (!sellerHasStoredSession()) {
      setSellerLoggedIn(false);
    } else {
      setSellerLoggedIn(true);
    }
  };

  const handleSellerUnauthorized = () => {
    setSellerLoggedIn(false);
  };

  const sellerLoginGate = view === "satici" && !sellerLoggedIn;

  return (
    <>
      <ChatInterface
        mode={view}
        onModeChange={handleModeChange}
        onSellerUnauthorized={handleSellerUnauthorized}
        onSellerLogout={handleLogout}
      />
      {sellerLoginGate ? (
        <div
          className="seller-login-overlay"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 10000,
            overflow: "auto",
          }}
          aria-modal="true"
          role="dialog"
          aria-label="Kooperatif girişi"
        >
          <LoginPage
            onLoginSuccess={handleLoginSuccess}
            onCancel={() => setView("musteri")}
          />
        </div>
      ) : null}
    </>
  );
}
