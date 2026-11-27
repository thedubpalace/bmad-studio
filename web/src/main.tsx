import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { SessionProvider } from "./auth/SessionContext";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/flow-editor.css";
import "./styles/bmad.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter>
        <SessionProvider>
          <App />
        </SessionProvider>
      </BrowserRouter>
    </StrictMode>,
  );
}
