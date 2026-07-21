import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Cross-origin isolation headers. These make `self.crossOriginIsolated === true`, which is what
// lets the Pocket TTS worker run onnxruntime-web WASM MULTI-THREADED instead of single-threaded
// — the single-threaded path synthesizes slower than real-time on many machines, which is what
// makes the agent's voice break up during playback. COEP is set to `credentialless` (not the
// stricter `require-corp`) so cross-origin resources we load without credentials — the ONNX
// Runtime CDN, the MediaPipe model/wasm CDN, and the Hugging Face TTS model bundle — still work
// without needing them to send CORP headers.
//
// If the app ever fails to load a cross-origin resource because of this, remove this block and
// audio will fall back to single-threaded (slower) synthesis. In production, set the same two
// headers on whatever serves the built app.
const crossOriginIsolationHeaders = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'credentialless',
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  preview: {
    headers: crossOriginIsolationHeaders,
  },
  server: {
    // host:true binds 0.0.0.0 so other devices on the LAN can reach the dev server;
    // allowedHosts:true accepts requests for any Host header (e.g. a LAN IP or a tunnel domain).
    // Dev-only conveniences. NOTE: camera/mic/AudioWorklet still require a SECURE context —
    // plain http://<lan-ip> is blocked by browsers; use HTTPS (a tunnel, or a local cert) to run
    // a real interview from another device. See docs / the PreJoin secure-context message.
    host: true,
    allowedHosts: true,
    port: 5173,
    headers: crossOriginIsolationHeaders,
    // Both services now serve their routes natively under /api and /ai (see backend/app/main.py
    // and ai_service/app/main.py), so this proxy is a PURE passthrough — no path rewriting. This
    // mirrors production nginx exactly: one location block per prefix, no prefix-stripping footgun.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ai': {
        target: 'http://localhost:8100',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
