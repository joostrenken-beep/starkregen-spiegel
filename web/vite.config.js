import { defineConfig } from 'vite';

// base './' -> funktioniert auf GitHub Pages unter /<repo>/ ohne Anpassung
export default defineConfig({
  base: './',
  build: { outDir: 'dist', assetsInlineLimit: 0, chunkSizeWarningLimit: 1200 },
});
