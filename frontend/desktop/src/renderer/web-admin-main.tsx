import React from "react";
import ReactDOM from "react-dom/client";
import { AdminApp } from "./AdminApp";
import { installWebBridge } from "./web-bridge";
import "./styles.css";

installWebBridge();
ReactDOM.createRoot(document.getElementById("admin-root")!).render(<React.StrictMode><AdminApp /></React.StrictMode>);
