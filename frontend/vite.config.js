import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Cesium is loaded via CDN <script> tag in index.html rather than npm +
// vite-plugin-cesium. Simpler build config, no static-asset copy step,
// works out of the box on Netlify. Swap to the npm package later if you
// want offline/self-hosted Cesium assets instead of the CDN build.
export default defineConfig({
  plugins: [react()],
});
