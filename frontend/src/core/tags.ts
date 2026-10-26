/* 标签显示名：纯查表，无 DOM 或请求，Application 与 React 页面共用本模块。
 * 映射只影响显示名，不做同义词归并；数据库标签名仍是真相，查不到时原样返回。 */
const TAG_DISPLAY_NAMES:Record<string,string>={'1080P':'1080p','60fps':'60FPS','AI去码':'AI解码',
  '眼镜':'眼镜娘','情趣内衣':'性感内衣',
  '口罩遮脸':'口罩','强制剧情':'强制','骑乘':'骑乘位',
  '后入':'背后位','3P多人':'3P','双洞齐插':'双洞齐下','毒龙':'毒龙钻'};
/** 标签键到界面上的名称；表里没有就原样返回。 */
const tagLabel=(tag:string)=>TAG_DISPLAY_NAMES[tag]||tag;

export {TAG_DISPLAY_NAMES, tagLabel};
