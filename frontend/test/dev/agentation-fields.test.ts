import { describe, expect, it } from 'vitest';

import { identifyAgentationFields } from '../../src/dev/agentation-fields';

describe('标注工具字段身份', () => {
  it('已有身份保留，动态加入的字段获得各自的名称', async () => {
    const host = document.createElement('div');
    host.innerHTML = '<input id="existing"><textarea name="comment"></textarea><textarea aria-label="Webhook URL"></textarea>';
    const stop = identifyAgentationFields(host);
    try {
      expect(host.querySelector('input')!.name).toBe('');
      expect(host.querySelector('[name="comment"]')).not.toBeNull();
      const webhook = host.querySelector<HTMLTextAreaElement>('[aria-label="Webhook URL"]')!;
      expect(webhook.name).not.toBe('');
      const comment = document.createElement('textarea');
      host.append(comment);
      await new Promise(resolve => setTimeout(resolve, 0));
      expect(comment.name).not.toBe('');
      expect(comment.name).not.toBe(webhook.name);
      host.append(document.createElement('span'));
      await new Promise(resolve => setTimeout(resolve, 0));
      expect(webhook.name).not.toBe(comment.name);
      const shadowHost = document.createElement('div');
      const shadow = shadowHost.attachShadow({ mode: 'open' });
      shadow.innerHTML = '<textarea aria-label="Shadow comment"></textarea>';
      host.append(shadowHost);
      await new Promise(resolve => setTimeout(resolve, 0));
      const shadowComment = shadow.querySelector('textarea')!;
      expect(shadowComment.name).not.toBe('');
      expect(shadowComment.name).not.toBe(comment.name);
      const nested = document.createElement('input');
      shadow.append(nested);
      await new Promise(resolve => setTimeout(resolve, 0));
      expect(nested.name).not.toBe('');
    } finally {
      stop();
    }
  });
});
