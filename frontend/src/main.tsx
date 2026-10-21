import "antd/dist/reset.css";
import "./antdReact19";
import { App as AntApp, ConfigProvider, theme } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#365dba",
          colorSuccess: "#39755b",
          colorWarning: "#a36a1e",
          colorError: "#bc4545",
          colorInfo: "#365dba",
          colorText: "#252b36",
          colorTextSecondary: "#69717e",
          colorBgBase: "#ffffff",
          colorBgContainer: "#ffffff",
          colorBorder: "#dce0e6",
          borderRadius: 6,
          controlHeight: 36,
          fontSize: 13,
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
          fontFamilyCode:
            "'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', monospace",
        },
        components: {
          Button: {
            controlHeightLG: 46,
            primaryShadow: "none",
            defaultShadow: "none",
          },
          Input: {
            activeBorderColor: "#365dba",
            hoverBorderColor: "#9aaaca",
          },
        },
      }}
    >
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
