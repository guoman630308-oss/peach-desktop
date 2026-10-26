/* 界面标注工具的构建配置：`build/agentation/peach-agentation.js`。
 *
 * 只由 `npm run build:agentation` 触发，不在 `npm run build` 里：产物不进 Git、不进独立包，
 * 由服务端的 `/dev/agentation.js` 从本机 `build/` 下读取（docs/FRONTEND.md「界面标注」）。
 * React 打进产物里，不借页面那份：主包 `peach-app.js` 不导出 React，也不该为一件开发工具改契约。 */
import { defineConfig } from 'vite';

export default defineConfig({
  // 库模式不替换 `process.env.NODE_ENV`，同 `vite.config.ts`。
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../build/agentation',
    emptyOutDir: true,
    target: 'es2022',
    minify: 'oxc',
    sourcemap: false,
    lib: {
      entry: 'src/dev/agentation.tsx',
      formats: ['es'],
      fileName: () => 'peach-agentation.js',
    },
    rollupOptions: { output: { codeSplitting: false } },
  },
});
