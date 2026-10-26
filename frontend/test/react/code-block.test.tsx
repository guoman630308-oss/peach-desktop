import { describe, expect, it, vi } from 'vitest';
import { CodeBlock } from '../../src/react/components/code-block';
import { buttonNamed, click, mount, settle } from './render';

describe('只读代码块', () => {
  it('展示纯文本且复制完整原文，包括被截断的内容', async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const code = '<script>不可执行</script>\n"长文本"';
    const host = await mount(<CodeBlock code={code} language="json" label="测试代码" maxCharacters={12} />);
    expect(host.querySelector('pre')?.textContent).toBe(code.slice(0, 12));
    expect(host.querySelector('script')).toBeNull();
    expect(host.querySelector('pre')?.tabIndex).toBe(0);
    await click(buttonNamed('复制完整内容', host));
    await settle();
    expect(writeText).toHaveBeenCalledWith(code);
    expect(host.querySelector('[role="status"]')?.textContent).toBe('已复制');
  });

  it('剪贴板被拒绝时保留可选中的原文和可重试按钮', async () => {
    const writeText = vi.fn(async () => { throw new Error('blocked') });
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const host = await mount(<CodeBlock code={'a = "b"'} language="toml" label="配置代码" />);
    await click(buttonNamed('复制代码', host));
    await settle();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('复制失败');
    expect(host.querySelector('code')?.textContent).toBe('a = "b"');
    expect(buttonNamed('复制代码', host)).not.toBeNull();
  });
});
