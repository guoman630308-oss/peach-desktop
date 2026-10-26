import { afterEach, expect, it, vi } from 'vitest';
import { mountPlayerAmbient } from '../src/player/controls';
import { configurePlayer } from '../src/player/host';

afterEach(()=>{vi.restoreAllMocks();document.body.innerHTML=''});

it('平均色微小抖动保持稳定，显著变化和关闭再开启更新氛围色',()=>{
  document.body.innerHTML='<dialog id="stage"><canvas data-ambient-canvas></canvas><video></video></dialog>';
  const stage=document.querySelector<HTMLDialogElement>('dialog')!,video=document.querySelector('video')!;
  let enabled=true,pixel=100,frame:VideoFrameRequestCallback;
  configurePlayer({stage:()=>stage,settings:()=>({ambientMode:enabled,theaterMode:false,miniplayer:true,detailAutoplay:false,javImage:'cover',seekSeconds:10}),saveSettings:()=>{},toast:()=>{},loadSourceStatus:async()=>({}),offlineReason:()=>''});
  Object.defineProperties(video,{readyState:{value:2},paused:{value:false},requestVideoFrameCallback:{value:(callback:VideoFrameRequestCallback)=>{frame=callback;return 1}}});
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({drawImage:vi.fn(),clearRect:vi.fn(),getImageData:()=>({data:new Uint8ClampedArray([pixel,pixel,pixel,255])})} as unknown as CanvasRenderingContext2D);
  const stop=mountPlayerAmbient(video);
  expect(stage.style.getPropertyValue('--video-glow')).toBe('rgb(100 100 100)');
  pixel=102;frame!(500,{} as VideoFrameCallbackMetadata);
  expect(stage.style.getPropertyValue('--video-glow')).toBe('rgb(100 100 100)');
  pixel=120;frame!(1000,{} as VideoFrameCallbackMetadata);
  expect(stage.style.getPropertyValue('--video-glow')).toBe('rgb(120 120 120)');
  enabled=false;document.dispatchEvent(new CustomEvent('peachambientchange',{detail:{enabled}}));
  expect(stage.style.getPropertyValue('--video-glow')).toBe('');
  enabled=true;pixel=121;document.dispatchEvent(new CustomEvent('peachambientchange',{detail:{enabled}}));
  expect(stage.style.getPropertyValue('--video-glow')).toBe('rgb(121 121 121)');
  stop();expect(stage.style.getPropertyValue('--video-glow')).toBe('');
});
