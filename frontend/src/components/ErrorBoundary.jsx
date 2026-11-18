import React from "react";

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Log diagnostic context without crashing the UI shell
    if (typeof console !== "undefined" && console.error) {
      console.error("Unhandled UI error:", error, errorInfo);
    }
  }

  handleReload = () => {
    if (typeof window !== "undefined" && window.location?.reload) {
      window.location.reload();
    }
  };

  render() {
    if (this.state.hasError) {
      const detail = this.state.error?.message || "Terjadi kesalahan tak terduga saat memuat tampilan.";
      return (
        <div
          role="alert"
          style={{
            maxWidth: 520,
            margin: "64px auto",
            padding: 24,
            borderRadius: 12,
            border: "1px solid #fecaca",
            background: "#fef2f2",
            color: "#991b1b",
            display: "grid",
            gap: 12,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 18 }}>Terjadi kesalahan pada aplikasi</h2>
          <p style={{ margin: 0, fontSize: 14, color: "#7f1d1d" }}>{detail}</p>
          <div>
            <button
              type="button"
              onClick={this.handleReload}
              style={{
                padding: "9px 16px",
                borderRadius: 8,
                border: "none",
                background: "#dc2626",
                color: "#fff",
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              Muat Ulang Halaman
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
