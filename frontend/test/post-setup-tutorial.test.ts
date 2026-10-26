/* 安装教程的状态层。卡片还画在遗留层，但「做到哪了」是这个模块说了算，
 * 所以跳过、折叠、跨页常驻和「签名没变就不重绘」这四条按行为验，不读 app.js 的源码。 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  POST_SETUP_TUTORIAL_COLLAPSED_KEY,
  POST_SETUP_TUTORIAL_KEY,
  POST_SETUP_TUTORIAL_SKIPPED_KEY,
  isCurrentPostSetupTutorialRequest,
  nextPostSetupTutorialRequest,
  postSetupTutorialCollapsed,
  postSetupTutorialMarker,
  postSetupTutorialSignature,
  postSetupTutorialSkipped,
  resetPostSetupTutorialState,
  setPostSetupTutorialCollapsed,
  setPostSetupTutorialMarker,
  setPostSetupTutorialSkipped,
} from '../src/onboarding/post-setup-tutorial';

const task = (key: string, done = false) =>
  ({ key, done, label: `做 ${key}`, description: '还没开始。', href: `/${key}` });

beforeEach(() => {
  localStorage.clear();
});

describe('安装教程的状态', () => {
  it('没写过任何键时是干净的未开始状态', () => {
    expect(postSetupTutorialMarker()).toBe('');
    expect(postSetupTutorialCollapsed()).toBe(false);
    expect([...postSetupTutorialSkipped()]).toEqual([]);
  });

  it('跳过的项目逐条累积，撤销只去掉那一条', () => {
    const skipped = postSetupTutorialSkipped();
    skipped.add('history');
    skipped.add('follow');
    setPostSetupTutorialSkipped(skipped);
    expect([...postSetupTutorialSkipped()].sort()).toEqual(['follow', 'history']);
    const restored = postSetupTutorialSkipped();
    restored.delete('history');
    setPostSetupTutorialSkipped(restored);
    expect([...postSetupTutorialSkipped()]).toEqual(['follow']);
  });

  it('跳过清单坏掉时当成没跳过，不让教程整块打不开', () => {
    localStorage.setItem(POST_SETUP_TUTORIAL_SKIPPED_KEY, '{不是数组');
    expect([...postSetupTutorialSkipped()]).toEqual([]);
    localStorage.setItem(POST_SETUP_TUTORIAL_SKIPPED_KEY, '{"history":true}');
    expect([...postSetupTutorialSkipped()]).toEqual([]);
  });

  it('折叠状态写进本地，换一页读到的还是它', () => {
    setPostSetupTutorialCollapsed(true);
    expect(localStorage.getItem(POST_SETUP_TUTORIAL_COLLAPSED_KEY)).toBe('1');
    expect(postSetupTutorialCollapsed()).toBe(true);
    setPostSetupTutorialCollapsed(false);
    expect(postSetupTutorialCollapsed()).toBe(false);
  });

  it('未完成标记不看当前是哪一页，跨页一直成立', () => {
    setPostSetupTutorialMarker('pending');
    expect(localStorage.getItem(POST_SETUP_TUTORIAL_KEY)).toBe('pending');
    for (const path of ['/', '/taste', '/subscriptions', '/review']) {
      history.replaceState({}, '', path);
      expect(postSetupTutorialMarker()).toBe('pending');
    }
    setPostSetupTutorialMarker('complete');
    expect(postSetupTutorialMarker()).toBe('complete');
  });

  it('清单内容没变时签名一致，任何一处变了签名就变', () => {
    const tasks = [task('library', true), task('history')];
    expect(postSetupTutorialSignature(tasks)).toBe(postSetupTutorialSignature([...tasks]));
    expect(postSetupTutorialSignature([task('library', true), task('history', true)]))
      .not.toBe(postSetupTutorialSignature(tasks));
    expect(postSetupTutorialSignature([task('library', true)]))
      .not.toBe(postSetupTutorialSignature(tasks));
    const renamed = [task('library', true), { ...task('history'), description: '已导入 3 份。' }];
    expect(postSetupTutorialSignature(renamed)).not.toBe(postSetupTutorialSignature(tasks));
  });

  it('只有最后发出的那一次取数算数', () => {
    const first = nextPostSetupTutorialRequest();
    const second = nextPostSetupTutorialRequest();
    expect(isCurrentPostSetupTutorialRequest(first)).toBe(false);
    expect(isCurrentPostSetupTutorialRequest(second)).toBe(true);
  });

  it('重新打开清掉跳过与折叠，标记回到未完成', () => {
    setPostSetupTutorialMarker('complete');
    setPostSetupTutorialCollapsed(true);
    setPostSetupTutorialSkipped(new Set(['history']));
    resetPostSetupTutorialState();
    expect(postSetupTutorialMarker()).toBe('pending');
    expect(postSetupTutorialCollapsed()).toBe(false);
    expect([...postSetupTutorialSkipped()]).toEqual([]);
  });
});
