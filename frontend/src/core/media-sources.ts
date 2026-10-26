/* 挂载来源与服务端共用 resources/media-sources.json。 */
import registry from '../../../resources/media-sources.json' with { type: 'json' };

export type SourceCost = 'free' | 'metered';
export const MEDIA_SOURCE_REGISTRY = registry;
export const MEDIA_SOURCE_OPTIONS = registry.map((source) =>
  [source.id, source.label] as const);
export const SCANNABLE_LOCATIONS = registry.map((source) => source.id);
export const CLOUD_LOCATIONS = registry.filter((source) => source.cloud).map((source) => source.id);
export const SOURCE_LABELS: Record<string, string> = Object.fromEntries(registry.map((source) => [source.id, source.name]));
export const sourceDefaultCost = (location: string): SourceCost =>
  registry.find((source) => source.id === location)?.default_cost === 'free' ? 'free' : 'metered';
