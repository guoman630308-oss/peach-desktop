/* 界面标注工具 Agentation 的挂载入口，只由 `vite.agentation.config.ts` 构建。
 *
 * 产物落在 `build/agentation/`，不进 Git 也不进独立包；Application 在本机开关打开时才
 * `import('/dev/agentation.js')`。它自带一份 React，挂在 `<body>` 末尾自己的根上，
 * 和页面里的 React 子树互不相干。用法见 docs/FRONTEND.md「界面标注」。 */
import { Agentation } from 'agentation';
import { createRoot } from 'react-dom/client';

import { identifyAgentationFields } from './agentation-fields';

/** 本机 `agentation-mcp` 的 HTTP 端口。服务没起时标注照样存在浏览器里，起来后自动补传。 */
const ENDPOINT = 'http://localhost:4747';

const host = document.createElement('div');
host.dataset.agentation = '';
identifyAgentationFields(host);
document.body.append(host);
// Peach 自己占着全局快捷键（Esc 收起面板、方向键翻图），标注工具只走工具栏按钮。
createRoot(host).render(
  <Agentation appName="Peach" endpoint={ENDPOINT} portalContainer={host} enableKeyboardShortcuts={false} />,
);
