// foxreach's icon (extension/icons/icon.svg) for MCP clients that show a server's icon:
// serverInfo.icons, new in protocol 2025-11-25. Kept here as text because the single-file build
// carries only imported modules, and Node.js cannot import an SVG; test/server_icon.py checks that
// it still matches the SVG.
import { Buffer } from "node:buffer";

export const ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path fill="#ff0000" d="M6 6l10 8h16l10-8-3 20c-1 8-8 16-15 16S10 34 9 26z"/><path fill="#0a5cff" d="M6 6l5.5 4.4L7.2 14zM42 6l-5.5 4.4L40.8 14z"/><path fill="none" stroke="#141414" stroke-width="1.6" stroke-linejoin="round" d="M6 6l10 8h16l10-8-3 20c-1 8-8 16-15 16S10 34 9 26z"/><path fill="#ffffff" d="M12 25c5 0 9 3 12 9 3-6 7-9 12-9-1 6.5-6 14-12 14s-11-7.5-12-14z"/><circle cx="17" cy="22" r="2.5" fill="#141414"/><circle cx="31" cy="22" r="2.5" fill="#141414"/><path fill="#141414" d="M21.2 34.8h5.6L24 38.4z"/></svg>';

export const SERVER_ICONS = [
  { src: `data:image/svg+xml;base64,${Buffer.from(ICON_SVG).toString("base64")}`, mimeType: "image/svg+xml", sizes: ["any"] },
];
