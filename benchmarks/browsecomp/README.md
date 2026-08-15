# BrowseComp

题库在运行时从 OpenAI 官方公开地址下载并校验，不进入 Git。正式题目与答案不得出现在报告中。
实现固定官方 query/grader prompt；上游脚本的 regex `group(0)` 与后续 `yes/no` 比较不一致，本实现采用其明确意图的 `group(1)`，并在证据中披露该补丁。
