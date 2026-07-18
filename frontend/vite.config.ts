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
    port: 5173,
    headers: crossOriginIsolationHeaders,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      '/ai': {
        target: 'http://localhost:8100',
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/ai/, ''),
      },
    },
  },
})
