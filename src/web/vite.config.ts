import { cp } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath, URL } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'copy-client-helpers',
      apply: 'build',
      async writeBundle(output) {
        if (!output.dir) {
          throw new Error('Build output directory is missing.');
        }
        await cp(
          fileURLToPath(
            new URL('../../support-files/scripts', import.meta.url),
          ),
          resolve(output.dir, 'helper'),
          {
            recursive: true,
          },
        );
      },
    },
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: [
      './src/test-setup.ts',
    ],
    include: [
      'src/**/*.test.{ts,tsx}',
    ],
  },
});
