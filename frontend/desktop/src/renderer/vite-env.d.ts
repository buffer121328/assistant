/// <reference types="vite/client" />

import type { AssistantDesktopApi } from "../preload";

declare global {
  interface Window {
    assistantDesktop: AssistantDesktopApi;
  }
}
