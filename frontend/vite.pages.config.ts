/* 独立页面包的构建配置：`web/dist/peach-pages.js` 与 `web/dist/peach-pages.css`。
 *
 * 首启、登录与错误三页由服务端吐同一张薄壳，只加载这两份；它们不要会话就能取（ADR-0094）。
 * 它们自成一体：React、BoardUI 控件和用到的
 * `src/ui-kit/` 共用件都打进来，产物没有外部 import，页面不加载主包 `peach-app.js`，同一个模块在这一页上不会有第二份实例。
 * `@peach/legacy/ui` 落到 `src/react/pages/legacy-ui.ts`，只转出共用件真正读的那几样。
 *
 * `npm run build` 里排在第一段之后（第一段会清空 web/dist）。不加内容哈希，理由同 `vite.config.ts`。
 * Tailwind、`@` 别名、NODE_ENV 替换与 CSS 目标同 `vite.config.ts`。 */
import { fileURLToPath } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [tailwindcss()],
  resolve: {
    alias: {
      '@peach/legacy/ui': fileURLToPath(new URL('./src/react/pages/legacy-ui.ts', import.meta.url)),
      '@': fileURLToPath(new URL('./src/react/boardui', import.meta.url)),
    },
  },
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../web/dist',
    emptyOutDir: false,
    target: 'es2022',
    cssTarget: ['chrome111', 'edge111', 'firefox128', 'safari16.4', 'ios16.4'],
    minify: 'oxc',
    sourcemap: false,
    cssCodeSplit: false,
    lib: {
      entry: 'src/react/pages/index.tsx',
      formats: ['es'],
      fileName: () => 'peach-pages.js',
    },
    rollupOptions: {
      output: {
        codeSplitting: false,
        assetFileNames: 'peach-pages.[ext]',
      },
    },
  },
});
