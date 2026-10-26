"""对外请求使用同一份标准桌面 Chrome User-Agent。

与用户这台机器上的 Chrome 保持一致，不只是为了像浏览器：Cloudflare 验证后发下的 `cf_clearance`
绑着解题那台浏览器的 UA，FC2PPV-DB 与 JAVten 这两个来源带着用户贴的 Cookie 请求时，UA 必须是同一个
（ADR-0060）。Chrome 大版本升了就改这里，全部对外请求一起换。
"""

# Chrome 153，2026-09-24 按用户 Chrome 的 `chrome://version` 核对；采用标准缩减版本格式。
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
