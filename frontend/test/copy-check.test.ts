import { describe, expect, it } from 'vitest';
import { resolve } from 'node:path';
import { scanSource, sourceFiles } from '../scripts/copy-check.mjs';

describe('源码文案解析', () => {
  it('变量、属性键和模块路径不属于叙述', () => {
    expect(scanSource('sample.ts', `import previously from 'used to';
      const formerly = { 'previously': 1 }; void formerly; void previously;`)).toEqual([]);
  });
  it('读取注释、字符串、模板与 JSX 正文', () => {
    for (const source of [
      '// 以前这里是旧实现\nconst x = 1;',
      'const x = "以前这里是旧实现";',
      'const x = `以前这里是旧实现 ${value}`;',
      'const x = <p>以前这里是旧实现</p>;',
    ]) {
      expect(scanSource('sample.tsx', source).some(item => item.severity === 'error')).toBe(true);
    }
  });
  it('保留逐行豁免并报告语法错误', () => {
    const source = '// 以前这里是旧实现 // copy-lint-disable-line\n// 以前这里是旧实现';
    expect(scanSource('sample.ts', source).map(item => item.line)).toEqual([2]);
    expect(scanSource('sample.ts', 'const = ;').some(item => item.severity === 'error')).toBe(true);
  });
  it('条件操作说明只提示核对', () => {
    const found = scanSource('sample.ts', '// 如果以前配置过代理，请核对地址。');
    expect(found.length).toBeGreaterThan(0);
    expect(found.every(item => item.severity === 'warning')).toBe(true);
  });
  it('收集实际源码且支持当前 TypeScript 和 JSX 语法', () => {
    expect(sourceFiles(resolve('src')).length).toBeGreaterThan(0);
    expect(scanSource('sample.tsx', 'type Pair = [symbol: string, value: number]; const a = <a href="/?x=1&thumb=0" />;')).toEqual([]);
  });
});
