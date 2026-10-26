/* 裁剪封面的数据契约。
 *
 * 只有一个写端点：给框就是设成手工取景，给 null 就是按折痕判据重算一遍。
 * 框是源图像素、右下开区间，与 `poster_box` 那个只读字段同一套坐标。 */
import { apiSend } from '../../api';
import type { CropBox } from '../../crop-geometry';

export const COVER_CROP_URL = '/api/cover-crop';

export interface CoverCropResult {
  ok: boolean;
  code: string;
  poster_box: { x0: number; y0: number; x1: number; y1: number; method: string; px: number[] } | null;
}

export const sendCoverCrop = (code: string, box: CropBox | null) =>
  apiSend<CoverCropResult>(COVER_CROP_URL, { code, box });
