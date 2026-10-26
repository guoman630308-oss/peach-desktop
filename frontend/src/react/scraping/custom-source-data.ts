import { apiGet } from '../../api';

export interface CustomSource {
  id?: string; name: string; endpoint: string; enabled: boolean; network?: string; code_format?: string;
  fields: Record<string, string>; content_types: string[];
}
export const CUSTOM_SOURCES_URL = '/api/custom-metadata';
export const CUSTOM_SOURCES_KEY = ['custom-metadata'] as const;
export const fetchCustomSources = (signal?: AbortSignal) =>
  apiGet<{ items: CustomSource[] }>(CUSTOM_SOURCES_URL, signal);
