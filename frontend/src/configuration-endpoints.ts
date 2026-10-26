/* 配置页的端点。首屏取数和挂载状态那一块的刷新读的是同一条，两份产物各打包一份这个模块。 */
export const CONFIGURATION_URL = '/api/configuration';

/** 让运行 Peach 的这台电脑弹系统文件夹对话框；浏览器自己拿不到本机绝对路径。 */
export const PICK_FOLDER_URL = '/api/pick-folder';
