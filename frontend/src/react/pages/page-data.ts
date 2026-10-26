/* HTML 模板挂载点上的 `data-*`：键是去掉 `data-` 前缀后的驼峰名，值都是字符串，服务端没给的键不在。 */
export type PageData = Readonly<Record<string, string | undefined>>;
