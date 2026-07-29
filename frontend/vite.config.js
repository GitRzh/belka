import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'

// Cesium needs its static assets (workers, textures) copied into the build;
// vite-plugin-cesium handles that + sets window.CESIUM_BASE_URL for us.
export default defineConfig({
  plugins: [react(), cesium()],
  server: {
    port: 5173,
  },
})
