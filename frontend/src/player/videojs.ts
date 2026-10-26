/* Video.js 10 浏览器模块按需从本地加载，全部相对导入由 vendor 入口固定。 */
import type { VideojsFactory } from './types';
import { createV10Player } from './v10-player';

const VIDEOJS = '/vendor/videojs/10.1.0/';
const MODULES = [
  'video-player.js', 'media/hlsjs-video.js', 'media/dash-video.js', 'ui/container.js', 'ui/play-button.js',
  'ui/mute-button.js', 'ui/fullscreen-button.js', 'ui/pip-button.js', 'ui/time-slider.js',
  'ui/volume-slider.js', 'ui/slider-track.js', 'ui/slider-fill.js', 'ui/slider-buffer.js',
  'ui/slider-thumb.js', 'ui/slider-value.js', 'i18n.js', 'locales/zh-CN.js',
];

let loader: Promise<VideojsFactory> | null = null;

export function ensureVideojs(): Promise<VideojsFactory> {
  loader ??= Promise.all(MODULES.map(name => import(/* @vite-ignore */ `${VIDEOJS}${name}`)))
    .then(() => createV10Player)
    .catch((error: unknown) => { loader = null; throw error });
  return loader;
}
