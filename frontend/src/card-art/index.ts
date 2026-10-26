/* 卡片图片、人脸取景与悬停预览（@peach/card-art）。
 * Application 与 React 页面引用同一份源码，随 peach-app.js 发出。
 * 代表作表、悬停配置与文档监听全站只有一份。 */
export { faceFrame, faceZoom, hasFaceBox, FACE_CEILING, FACE_TARGET, MIN_FACE_PX } from './face-frame';
export type { FaceBox, FrameSize } from './face-frame';
export { advanceImageFallback, imageFallbackAttrs, parseFallbacks, wireImageFallbacks } from './image-fallback';
export { faceSourceScale, nativeImageFit } from './native-image';
export { rememberRepresentatives, representativeOf } from './representatives';
export type { RepresentativeRow } from './representatives';
export {
  avatarInner, cardArtwork, cardIdentity, coverImage, coverUrl, detailPosterUrl, entityAvatar, entityFaceImg,
  faceBoxAttrs, faceOrigin, facePos, javArtwork, logoUrl, mixFace, mixLabel, performerLabel, queueAvatarHtml,
  queueThumbHtml, withVersion,
} from './markup';
export type { Artwork, ArtworkKind, CoverItem, FaceFocus, FaceRef, IdentityItem } from './markup';
export {
  avatarFrame, coverAnchor, coverFace, coverRatio, fitNativeImage, frameCachedImages, installCardArt,
  posterPanel, refitNativeImages, relayoutCovers, settleImage, upgradeCover, watchPendingImages,
} from './framing';
export { configureHoverPreview, releaseHover, releaseHoverPreviews, setHoverState, wireHover } from './hover';
export type { HoverItem, HoverPreviewConfig } from './hover';
