import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import EmbeddedEditor from "./components/EmbeddedEditor";
import "./index.css";
import "./App.css";
createRoot(document.getElementById("root")!).render(<StrictMode><EmbeddedEditor /></StrictMode>);
