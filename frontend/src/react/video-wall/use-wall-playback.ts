import { useEffect, useRef, useState, type RefObject } from 'react';

/** 墙内控制条监听共用播放器的 video，不挂第二份统计或流源。 */
export function useWallPlayback(id: number | null, frame: RefObject<HTMLDivElement | null>, ended: () => void) {
  const [state, setState] = useState({ paused:true, time:0, duration:0 });
  const video = useRef<HTMLVideoElement | null>(null);
  const onEnded = useRef(ended); onEnded.current = ended;
  useEffect(() => {
    const node = id ? frame.current?.querySelector('video') : null;
    video.current = node || null;
    if (!node) { setState({ paused:true, time:0, duration:0 }); return }
    const sync = () => setState({ paused:node.paused, time:node.currentTime,
      duration:Number.isFinite(node.duration) ? node.duration : 0 });
    const finish = () => onEnded.current();
    const events = ['play','pause','timeupdate','durationchange','loadedmetadata'];
    events.forEach(name => node.addEventListener(name, sync));
    node.addEventListener('ended', finish); sync();
    return () => {
      events.forEach(name => node.removeEventListener(name, sync));
      node.removeEventListener('ended', finish); video.current = null;
    };
  }, [id, frame]);
  return { ...state,
    toggle:async () => { if (video.current?.paused) await video.current.play(); else video.current?.pause() },
    seek:(seconds: number) => { if (video.current) video.current.currentTime = seconds },
  };
}
