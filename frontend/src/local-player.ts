import { ApiError, apiGet } from './api';

export interface LocalPlayerPolicy {
  available: boolean;
  mode: 'external' | 'internal';
  executable: string;
  label: string;
}
export const LOCAL_PLAYER_URL = '/api/configuration/local-player';
const INTERNAL: LocalPlayerPolicy = { available: false, mode: 'internal', executable: '', label: '外部播放器' };
export let localPlayerPolicy: LocalPlayerPolicy = INTERNAL;
let loaded = false;
let pending: Promise<LocalPlayerPolicy> | null = null;

export function rememberLocalPlayerPolicy(policy: LocalPlayerPolicy): LocalPlayerPolicy {
  localPlayerPolicy = policy;
  loaded = true;
  return policy;
}

export function resetLocalPlayerPolicy(): void {
  localPlayerPolicy = INTERNAL;
  loaded = false;
  pending = null;
}

export function loadLocalPlayerPolicy(): Promise<LocalPlayerPolicy> {
  if (loaded) return Promise.resolve(localPlayerPolicy);
  pending ??= apiGet<LocalPlayerPolicy>(LOCAL_PLAYER_URL).catch((error: unknown) => {
    if (error instanceof ApiError && [403, 404, 501].includes(error.status)) return INTERNAL;
    throw error;
  }).then(rememberLocalPlayerPolicy).finally(() => { pending = null });
  return pending;
}

export const prefersExternalPlayer = (): boolean => localPlayerPolicy.available && localPlayerPolicy.mode === 'external';
