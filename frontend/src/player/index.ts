/* 播放器模块：命令式 TypeScript，不进入 React 渲染。
 * Video.js 接管 video 宿主与控件 DOM；舞台和沉浸的 effect 调 mountPlayer 并保存清理函数。
 * Application 与 React 页面共享源码图，宿主、播放器槽位、流会话与右键菜单状态各只有一份。 */
export type { PlayerHost, PlayerSettings } from './host';
export { configurePlayer } from './host';
export type { PlayerMenuHooks } from './menu';
export { closePlayerMenu, configurePlayerMenu } from './menu';
export { applyAmbientMode, applyTheaterMode, clickPlayerControl } from './controls';
export type { DetailPlayerOptions, MountPlayerOptions, PlayerMedia, PlayerResume } from './detail-player';
export {
  attachPlayerChrome, mountDetailPlayer, mountPlayer, preparePlayerFrame, stopPlayerPanels,
} from './detail-player';
export { disposePlayer } from './dispose';
export type { ImmersePlayerOptions } from './immerse-player';
export { seekVideoBy, toggleVideoPlayback } from './playback';
export { detailPlayer, resizeSoon, setDetailPlayer } from './registry';
export { fmtSpeed, streamSpeedBits } from './stats';
export {
  cancelDetailStream, cancelStreamSession, directStreamSource, newStreamSession, playableStreamSource,
} from './stream';
export type { TelemetryOptions } from './telemetry';
export { wireFollowTelemetry, wireTelemetry } from './telemetry';
export type { PlayerItem, VjsPlayer } from './types';
export { ensureVideojs } from './videojs';
export { mountPreviewPlayer } from './preview-player';
