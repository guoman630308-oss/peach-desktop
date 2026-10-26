# 媒体来源图标

不登记到 `docs/reference-sources.json`：本文是二进制图标的取证记录，不是可重抓的上游 Markdown；原始文件与 PNG 的摘要分别列在下方。

取证日期：2026-09-06。官网图标仅用于标识对应服务，权利归各服务所有；Pillow 将原文件无缩放转为 PNG。图标内嵌在本地模块，使用时不请求外站。本地磁盘复用项目已固定版本的 Lucide hard-drive。

- 115: https://115.com/favicon.ico; source SHA-256 27217fc80a3ac0949acc55b43f9662a08bfab1026b6fee51e2a7f3151721b64a; PNG SHA-256 cb8761584ad38d6b44d1f50b2f9f6b57b1880243ccd88d435682c39d850e986a
- pikpak: https://mypikpak.com/favicon-32x32.png; source SHA-256 c8aa2053bfa44e1c0a052c5fe3cdc540fd42471756d3017b654e72e671e69914; PNG SHA-256 1fb0e2d400c9277654194343ffd3bb5b92410c8da56e7ccee65997b0e4cab4d8

2026-10-17 实测：光鸭官网 HTML 的 `link[rel=icon]` 指向 `/icon.png`；OneDrive 官网跳转到 Microsoft 产品页，其中 `img[alt=OneDrive]` 使用 FY26 SVG 品牌图标。通过 HTTPX 严格 HTTPS 下载并解码核对：光鸭原尺寸 32×32，用 Pillow 转为 PNG；OneDrive 复用 `peach.link_marks.rasterize_svg` 与锁定的 resvg-py 渲染为 64×64 PNG。两者与 115、PikPak 共用本地内嵌 PNG 和 16×16 显示方式；其他已挂载网盘使用通用 database 字形。

- guangya: https://www.guangyapan.com/icon.png; source SHA-256 ef293885f5713eef15e81f11cd4545d0fc93a9df98c7599b7db03c1ec65f64d1; PNG SHA-256 53fdba335e45ed9b0ecda742bdc8358eb616e964f010cb2682ad5b4f0ec6b7d8
- onedrive: https://www.microsoft.com/content/dam/microsoft/bade/images/icons/en-us/m365-app-icons-fy26/OneDrive-Icon-FY26.svg; source SHA-256 9f72ac359c7e03ca4723ce1a3b60f932a0bc5d2ce72dbdce365e83223142f6bc; PNG SHA-256 364c50cdfc37e563c33b6d0a3db2fc84dbdc91a73268a428f199815a57067944
