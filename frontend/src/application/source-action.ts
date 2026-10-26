import { esc } from '../core';
import { setActionBusy } from '../ui-kit/controls';
import { spinnerHtml } from '../ui-kit/markup';

export interface SourceActionPorts {
  request(id: number): Promise<unknown>;
  success(): void;
  failureText(error: unknown): string;
}
export interface SourceActionStatus { textContent: string | null }
export interface SourceActionOptions { button?: HTMLElement | null }

/** 定位请求的忙态、终态提示与按钮恢复；请求只提交资产 id。 */
export function createRevealSourceAction(ports: SourceActionPorts) {
  return async (id: number, status: SourceActionStatus, { button = null }: SourceActionOptions = {}): Promise<void> => {
    if (button?.getAttribute('aria-busy') === 'true') return;
    const html = button?.innerHTML ?? '', label = button?.textContent?.trim() ?? '';
    if (button) {
      setActionBusy(button);
      button.innerHTML = `${spinnerHtml('正在定位')}${label ? `<span>${esc(label)}</span>` : ''}`;
    }
    status.textContent = '';
    try {
      await ports.request(id);
      status.textContent = '';
      ports.success();
    } catch (error) {
      status.textContent = ports.failureText(error);
    } finally {
      if (button) { setActionBusy(button, false); button.innerHTML = html }
    }
  };
}
