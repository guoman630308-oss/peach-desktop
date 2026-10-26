/** 主界面根：同一模块图和路由树，资源跟随根的生命周期。 */
import { useLayoutEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { initializeApplication } from '../application/initialize.js';
import { connectApplication, disposeApplicationResidents } from './application-residents';
import { renewApplicationEffects, type ApplicationEffects } from '../application/effects';
import { initMiddleTruncate } from '../ui-kit/middle-truncate';
import { closeAnchoredMenu } from '../ui-kit/anchored-menu';
import { queryClient } from '../query';
import { releaseHoverPreviews } from '../card-art';
import { releaseAllManagedRoutes } from '../history';
import { RouterRoot, ROUTER_ROOT_OPTIONS } from './router/router';
import type { ShellActions } from './router/shell-actions';
import { ResultUpdates } from './result-updates';

interface ApplicationResources { effects: ApplicationEffects; owners: number }
let resources: ApplicationResources | null = null;

function finalizeResources(current: ApplicationResources): void {
  if (resources !== current || current.owners) return;
  resources = null;
  current.effects.dispose();
}

/** StrictMode 的同步重取沿用资源；最终卸载在微任务里统一清理。 */
function acquireResources(): ApplicationResources {
  if (!resources) {
    const effects = renewApplicationEffects();
    resources = { effects, owners: 0 };
    effects.own(initMiddleTruncate(document));
    effects.own(() => { releaseHoverPreviews(); void queryClient.cancelQueries(); });
    effects.own(disposeApplicationResidents);
    effects.own(releaseAllManagedRoutes);
    effects.own(closeAnchoredMenu);
  }
  resources.owners += 1;
  resources.effects.resume();
  return resources;
}

export function Application() {
  const [actions, setActions] = useState<ShellActions | null>(null);
  useLayoutEffect(() => {
    const first = resources === null;
    const current = acquireResources();
    const disconnect = connectApplication(setActions);
    if (first) {
      try { initializeApplication(); }
      catch (error) { disconnect(); current.effects.dispose(); resources = null; throw error; }
    }
    return () => {
      disconnect();
      current.owners -= 1;
      if (current.owners) return;
      current.effects.pause();
      queueMicrotask(() => finalizeResources(current));
    };
  }, []);
  return <RouterRoot actions={actions} processing background={actions ? <ResultUpdates /> : null} />;
}
const roots = new WeakMap<Element, Root>();
export function mountApplication(host: Element): Root {
  const current = roots.get(host);
  if (current) return current;
  const root = createRoot(host, ROUTER_ROOT_OPTIONS);
  roots.set(host, root);
  const unmount = root.unmount.bind(root);
  root.unmount = () => {
    if (roots.get(host) !== root) return;
    roots.delete(host);
    unmount();
    if (resources) finalizeResources(resources);
  };
  flushSync(() => root.render(<Application />));
  return root;
}
