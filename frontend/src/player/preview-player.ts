/* 来源视频预览复用 Peach 控件；播放状态只属于当前预览。 */
import { mountPlayerQualityControl, mountPlayerSeekPreview, mountPlayerSpinner } from './controls';
import { disposePlayer } from './dispose';
import type { PlayerSource, VjsPlayer } from './types';
import { ensureVideojs } from './videojs';

export function mountPreviewPlayer(video: HTMLVideoElement, source: PlayerSource, failed: () => void): () => void {
  video.controls = false;
  let released = false;
  let player: VjsPlayer | null = null;
  void ensureVideojs().then(factory => {
    if (released) return;
    player = factory(video, {
      controls: true, preload: 'metadata', autoplay: false, language: 'zh-CN', responsive: true,
      userActions: { hotkeys: true },
      controlBar: {
        pictureInPictureToggle: true, currentTimeDisplay: false, timeDivider: false,
        durationDisplay: false, remainingTimeDisplay: false,
        progressControl: { seekBar: { mouseTimeDisplay: false, playProgressBar: { timeTooltip: false } } },
      },
    });
    const current = player;
    current.on('error', () => { if (!released) failed() });
    current.ready(() => {
      if (released || current.isDisposed()) return;
      mountPlayerQualityControl(current, video);
      mountPlayerSeekPreview(current, null, { thumbnail: false });
      mountPlayerSpinner(current);
    });
    current.src(source);
  }).catch(() => { if (!released) failed() });
  return () => {
    released = true;
    disposePlayer(player);
    if (!player) { video.pause(); video.removeAttribute('src'); video.load(); video.remove() }
  };
}
