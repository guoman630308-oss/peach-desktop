import { useContext, useEffect } from 'react';
import { useLocation } from 'react-router';
import { isPreferencePath, peachHistory, preferenceSection } from '@peach/history';
import { ShellActionsContext } from '../router';

export function SettingsMatch() {
  const actions = useContext(ShellActionsContext);
  const { pathname } = useLocation();
  useEffect(() => { void actions?.openSettings?.(preferenceSection(pathname)) }, [actions, pathname]);
  useEffect(() => () => {
    queueMicrotask(() => { if (!isPreferencePath(peachHistory.navigation.location.pathname)) actions?.closeSettings?.() });
  }, [actions]);
  return null;
}
