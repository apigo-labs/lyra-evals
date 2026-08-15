# DRACO

题库在运行时从 Perplexity AI 的公开固定 revision 下载并校验，不进入 Git。插件复现
OpenRouter benchmark harness `77483ab` 的题目输入前缀、逐 criterion judge、正负权重、裁剪、
三次 judge 与 task macro average。

当前 APIGO VOHU 只暴露 Web Search，尚未提供 Fusion 历史运行使用的 Exa `web_search`、
`web_fetch`、`bash` 与 16 次工具调用的完全相同契约。因此插件可以做协议实验和内部 smoke，
但在工具等价前不得将 VOHU 分数与 Fusion 69.0 宣称为严格同口径胜负。
