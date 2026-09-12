# 官方评测依赖

- LiveCodeBench 的测试执行源码来自 MIT 许可的 [官方仓库](https://github.com/LiveCodeBench/LiveCodeBench)，Dockerfile 固定 commit 与文件 SHA-256；未修改原评分源码。包装器只接收样本/代码并返回评分，不在宿主执行生成代码。
- SWE-bench 和 τ²-bench 的官方 Python 包在独立 `.local/benchmark-runtime` 安装，源码 revision 由 adapters 模块冻结，第三方依赖由 runtime-requirements.txt 固定。
- 官方 GPQA 作者公开发布包采用 CC BY 4.0；数据归属 Irving David Rein。数据与标准答案不进入 Git，不用于训练；本地 prompt/shuffle 是明确的闭卷派生协议，不能宣称复现作者原提示。
- 原始数据准备会下载约 4.5 GB 的 LCB JSON，隐藏测试与 Agent 输入分别保存。全部原始内容保存在 `.local`，不通过 Console API 返回。

```sh
uv run --extra benchmarks python scripts/prepare_benchmark_runtimes.py
uv run --extra benchmarks python scripts/prepare_benchmarks.py ifeval gpqa livecodebench tau2 swebench
make benchmark-lcb-image
```

保存的模型答案可使用 `scripts/score_benchmark.py --help` 评分。IFEval/GPQA 只评分文本；LCB 只在断网评分镜像中执行代码；SWE 导出官方 predictions 和固定数据，显式 `--execute-swe` 使用预先冻结的实例环境执行官方 Docker evaluator。τ² 通过独立服务提供官方 Gym 工具并隔离隐藏状态，模拟器与 Judge 必须使用计划内 APIGO 参考模型。

## 交互执行与环境准备（2026-09-09）

τ² 使用官方 Gym 服务与 MCP 工具桥。Agent、模拟器/Judge、Gateway 分开容器；用户模拟器只经 APIGO，参考模型和额外预算冻结到计划，费用合并到同一题。构建运行镜像：`uv run --extra console python scripts/build_tau_runtime.py`；零模型调用验收：`uv run --extra console python scripts/verify_tau_runtime.py`。后者使用官方 mock 任务，不能充当真实领域成绩。

SWE 固定官方 **4.1.0**（`726c5461e2ef52d83cf1ea2107870a8bb3328d57`），与当前冻结的旧版 Verified 数据兼容。5.x 的新数据字段要求不同，不能直接替换。Console 冻结计划后点击“准备 SWE 实例环境”，只准备所选题目；也可使用独立运行时执行 `scripts/swe_runtime.py --cache .local/swe-specs --prepare <本地题目JSON>`。准备阶段可以下载官方镜像和构造 spec；评分阶段只能用冻结的本地镜像，断网、去除 capabilities，并限制 CPU、内存和进程数。

Agent 工作区从官方实例镜像复制，保留编译产物和环境兼容提交，核验题库 base commit 是其祖先。`swe_exec` 在相同官方依赖环境执行仓库命令，与 Agent 共享工作区；标准补丁和隐藏测试由独立评分容器持有。评分超时/取消会清理容器，官方基础设施错误不会伪装为正常错误答案。准备全部 500 个实例会占用大量磁盘，按计划准备即可。

账单在 Console“结果与导出 → 配置账单连接”配置平台访问令牌和 workspace ID。模型 API Key 不能替代平台凭据。每分钟自动同步或手动触发；精确费用需请求 ID、模型与 settled 状态匹配。未结算或证据缺失保持未知，重复同步不会重复累计。

内测 Fusion 别名应从鉴权后的 `/v1/models` 判断可用性，不能用公共价格目录代替模型目录。没有公开报价时，可在“模型连接 → 配置内测价格上限”填写供应商确认的输入/输出费率上限，覆盖缓存与推理计费；冻结计划保留来源 `operator_supplied_upper_bound`。没有已知费率上限时不发起付费调用，不能猜价或绕过已授权总预算。真实账单仍以请求级结算为准。

## SWE-bench Lite

新建 Console 计划优先选 Lite（官方 test 划分 300 题），也可选 Verified（500 题）。两者独立冻结，不覆盖已有题库；结果/逐题导出携带 subset。准备命令：`uv run --extra benchmarks python scripts/prepare_benchmarks.py swebench --swe-subset lite`。外部答案评分同样传 `--swe-subset lite`，默认仍为 verified 以兼容已有脚本。Lite 仍需逐题官方实例镜像和模型预算；抽样结果不能标为全量 Lite 分数。
