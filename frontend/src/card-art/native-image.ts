/** 手上这张相对边车那张源图缩了多少；换成了别的图就是 0。
 *
 *  索引页取的是实体图缩到长边 640 的派生件，人脸边车记的仍是原件的像素。取景的几何
 *  在等比缩放下不变，唯独「放到多大就开始虚」那一条问的是真有多少像素可用——拿原件的
 *  数去套派生件，算出来的倍数正好把一张图放糊。所以按这个比例把脸框换算到手上这张。
 *
 *  两轴各算一次再比：等比缩放只差取整那一点，差得多就说明这根本是另一张图。
 *  只认缩小：比源图还大的必然不是它缩出来的。 */
export function faceSourceScale(width: number, height: number, sourceWidth: number, sourceHeight: number) {
  if (!(width > 0 && height > 0 && sourceWidth > 0 && sourceHeight > 0)) return 0;
  const byWidth = width / sourceWidth, byHeight = height / sourceHeight;
  if (byWidth > 1.001 || byHeight > 1.001) return 0;
  if (Math.abs(byWidth - byHeight) > Math.max(byWidth, byHeight) * .01) return 0;
  return byWidth;
}

/** 64 px 以下的头像属于小图标；大图和 70 px 紧凑头像按源像素判断是否补底。 */
export function nativeImageFit(width: number, height: number, frameWidth: number, frameHeight: number, dpr = 1) {
  const density = Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
  width /= density;
  height /= density;
  const valid = [width, height, frameWidth, frameHeight].every(value => Number.isFinite(value) && value > 0);
  const small = valid && Math.min(frameWidth, frameHeight) >= 64
    && (width < frameWidth * .4 || height < frameHeight * .4);
  const scale = valid ? Math.min(1, frameWidth / width, frameHeight / height) : 1;
  return { small, width: width * scale, height: height * scale };
}

/** 标识按框 contain 之后，短边至少占框短边的这一份。10:1 的字标在方框里正好落在这条线上。 */
export const MARK_MIN_SHORT = 0.1;

/** 超扁、超高的标识（实测 1378×42、42×600）按框 contain 只剩一道线：短边撑到 `MARK_MIN_SHORT`，
 *  长边仍按框，摆的时候 cover，裁掉的是长边两头。比例正常的标识返回 `null`，照常 contain。 */
export function markClamp(width: number, height: number, frameWidth: number, frameHeight: number) {
  if (![width, height, frameWidth, frameHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const scale = Math.min(frameWidth / width, frameHeight / height);
  const shownWidth = width * scale, shownHeight = height * scale;
  const floor = Math.min(frameWidth, frameHeight) * MARK_MIN_SHORT;
  if (Math.min(shownWidth, shownHeight) >= floor) return null;
  return shownHeight < shownWidth ? { width: shownWidth, height: floor } : { width: floor, height: shownHeight };
}
