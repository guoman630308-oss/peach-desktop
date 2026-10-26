/* 主界面构建：Application、路由、常驻组件与共享状态打进 peach-app.js。
 * 固定文件名由 index.html 引用；服务端以 ETag 校验内容并要求每次重新验证缓存。
 * 模块别名直接指向唯一源码，浏览器仅在启用开发取证时请求独立的 agentation 模块。 */
import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const source = (name: string) => fileURLToPath(new URL(`./src/${name}`, import.meta.url));

export default defineConfig({
  plugins: [tailwindcss()],
  resolve: { alias: {
    'next/link': source('react/components/board-navigation-link.tsx'),
    '@peach/legacy/core': source('core/index.ts'), '@peach/legacy/ui': source('ui-kit/index.ts'),
    '@peach/legacy/jav-title': source('core/jav-title.ts'), '@peach/legacy/tags': source('core/tags.ts'),
    '@peach/legacy/ui-sounds': source('ui-kit/sounds.ts'), '@peach/legacy/middle-truncate': source('ui-kit/middle-truncate.ts'),
    '@peach/card-art': source('card-art/index.ts'), '@peach/appearance': source('appearance/index.ts'),
    '@peach/query': source('query/index.ts'), '@peach/history': source('history/index.ts'), '@peach/shell': source('shell/index.ts'),
    '@/registry': source('react/evilcharts/registry'), '@/lib/utils': source('react/charts/cn.ts'), '@': source('react/boardui'),
  } },
  // 库模式不替换 `process.env.NODE_ENV`。`QueryClient`（`@tanstack/query-core`）随这份产物发出，它的开发期
  // 告警按这个值判断，不替换的话浏览器里读到一个不存在的 `process`，整个模块加载失败。
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: '../web/dist',
    // 产物进 Git，所以目录必须只剩当前构建的东西；残留文件会被一起提交。
    emptyOutDir: true,
    target: 'es2022',
    cssTarget: ['chrome111','edge111','firefox128','safari16.4','ios16.4'],
    // Vite 8 的内核是 rolldown，压缩走 oxc；写 'esbuild' 会落到已废弃的转译插件上。
    minify: 'oxc',
    sourcemap: false,
    cssCodeSplit: false,
    lib: {
      entry: 'src/react/bootstrap.tsx',
      formats: ['es'],
      fileName: () => 'peach-app.js',
    },
    rollupOptions: {
      external: ['/dev/agentation.js'],
      output: {
        // island 之间不做代码分割：入口是浏览器直接 import 的单一模块。
        codeSplitting: false,
        assetFileNames: 'peach-app.[ext]',
      },
    },
  },
});
