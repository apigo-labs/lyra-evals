import { Billing } from "./Billing";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ZAxis,
} from "recharts";

const rowSchema = z.object({
  run_id: z.string(),
  run_name: z.string(),
  job_id: z.string(),
  benchmark: z.string(),
  subset: z.string().nullable().optional(),
  model: z.string(),
  harness: z.string(),
  effort: z.string(),
  effective_effort: z.string().nullable(),
  comparison: z.string(),
  synthetic: z.boolean(),
  status: z.string(),
  planned: z.number(),
  completed: z.number(),
  scored: z.number(),
  correct: z.number().nullable(),
  accuracy: z.number().nullable(),
  provisional_accuracy: z.number().nullable(),
  cost_usd: z.number().nullable(),
  estimated_cost_usd: z.number().nullable(),
  known_estimated_cost_usd: z.number().nullable(),
  requests_missing_usage: z.number(),
  reserved_usd: z.number().nullable(),
  billing_status: z.string(),
  cost_per_correct_usd: z.number().nullable(),
  latency_mean_ms: z.number().nullable(),
  latency_p50_ms: z.number().nullable(),
  latency_p95_ms: z.number().nullable(),
  latency_samples: z.number(),
  wall_time_ms: z.number().nullable(),
  metric: z.string(),
});
const fmt = (v: number | null, unit = "") =>
  v === null
    ? "—"
    : `${v.toLocaleString(undefined, { maximumFractionDigits: 4 })}${unit}`;

export function Results({ onRun }: { onRun: (id: string) => void }) {
  const [benchmark, setBenchmark] = useState("");
  const [model, setModel] = useState("");
  const [estimate, setEstimate] = useState(false);
  const [detail, setDetail] = useState<{ run: string; job: string } | null>(
    null,
  );
  const [synthetic, setSynthetic] = useState(false);
  const query = new URLSearchParams({ include_synthetic: String(synthetic) });
  if (benchmark) query.set("benchmark", benchmark);
  if (model) query.set("model", model);
  const result = useQuery({
    queryKey: ["results", query.toString()],
    queryFn: () =>
      api(`/results?${query}`, z.object({ rows: z.array(rowSchema) })),
    refetchInterval: 2000,
  });
  const rows = result.data?.rows ?? [];
  const settled = rows.filter((r) => !r.synthetic && r.cost_usd !== null);
  const points = rows
    .filter(
      (r) =>
        !r.synthetic &&
        r.accuracy !== null &&
        (estimate ? r.estimated_cost_usd : r.cost_usd) !== null &&
        r.latency_mean_ms !== null,
    )
    .map((r) => ({
      ...r,
      plot_cost: estimate ? r.estimated_cost_usd : r.cost_usd,
      accuracy_pct: r.accuracy! * 100,
      seconds: r.latency_mean_ms! / 1000,
    }));
  async function download(format: "csv" | "json", level = "jobs") {
    const a = document.createElement("a");
    a.href = `/api/results?${query}&format=${format}&level=${level}&download=true`;
    a.download = `lyra-${level}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  const [error, setError] = useState("");
  return (
    <div className="space-y-5">
      <Billing />
      <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-white p-4">
        <label className="text-xs">
          评测集{" "}
          <select
            aria-label="筛选评测集"
            className="ml-2 rounded border p-2"
            value={benchmark}
            onChange={(e) => setBenchmark(e.target.value)}
          >
            <option value="">全部</option>
            {["ifeval", "gpqa", "livecodebench", "tau2", "swebench"].map(
              (b) => (
                <option key={b}>{b}</option>
              ),
            )}
          </select>
        </label>
        <input
          aria-label="精确筛选模型"
          placeholder="模型名称（精确匹配）"
          className="rounded border p-2 text-xs"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={synthetic}
            onChange={(e) => setSynthetic(e.target.checked)}
          />
          显示沙箱自检
        </label>
        <div className="ml-auto flex gap-2">
          <Button
            variant="outline"
            onClick={() =>
              void download("csv", "episodes").catch((e) => setError(e.message))
            }
          >
            逐题 CSV
          </Button>
          {(["csv", "json"] as const).map((f) => (
            <Button
              key={f}
              variant="outline"
              onClick={() => void download(f).catch((e) => setError(e.message))}
            >
              导出 {f.toUpperCase()}
            </Button>
          ))}
        </div>
      </div>
      {(error || result.error) && (
        <p role="alert">{error || result.error?.message}</p>
      )}
      <div className="grid gap-4 md:grid-cols-3">
        {[
          [
            "已结算成本",
            settled.length
              ? `$${fmt(settled.reduce((s, r) => s + r.cost_usd!, 0))}`
              : "—",
            `${rows.filter((r) => !r.synthetic && r.billing_status === "pending").length} 个作业待结算`,
          ],
          [
            "完成样本 / 计划样本",
            `${rows.reduce((s, r) => s + r.completed, 0)} / ${rows.reduce((s, r) => s + r.planned, 0)}`,
            "失败和未完成样本保留在计划分母中",
          ],
          [
            "可查看的作业",
            String(rows.length),
            "按运行 × 评测集 × 模型变体保留独立成绩",
          ],
        ].map(([title, value, note]) => (
          <div key={title} className="rounded-xl border bg-white p-5">
            <p className="text-xs text-neutral-500">{title}</p>
            <p className="my-3 text-3xl font-semibold">{value}</p>
            <p className="text-xs text-neutral-500">{note}</p>
          </div>
        ))}
      </div>
      <section className="rounded-xl border bg-white p-5">
        <h2 className="font-semibold">成本 · 耗时 · 准确率</h2>
        <label className="mt-3 flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={estimate}
            onChange={(e) => setEstimate(e.target.checked)}
          />
          使用价格上界估算绘图（非账单）
        </label>
        <p className="mt-2 text-xs text-neutral-500">
          横轴为总费用，纵轴为准确率，圆点大小表示平均单题耗时。仅展示已完成且具有所选成本证据的真实作业；不同
          Harness 的结果属于系统对比。
        </p>
        {points.length ? (
          <div className="mt-6 h-64">
            <ResponsiveContainer>
              <ScatterChart>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  dataKey="plot_cost"
                  name="费用"
                  unit=" USD"
                />
                <YAxis
                  type="number"
                  dataKey="accuracy_pct"
                  name="准确率"
                  domain={[0, 100]}
                  padding={{ top: 12, bottom: 8 }}
                  unit="%"
                />
                <ZAxis
                  type="number"
                  dataKey="seconds"
                  name="平均耗时"
                  unit="s"
                  range={[60, 350]}
                />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  content={({ payload }) => {
                    const r = payload?.[0]?.payload;
                    return r ? (
                      <div className="rounded border bg-white p-3 text-xs">
                        {r.model} · {r.benchmark}
                        {r.subset ? ` / ${r.subset}` : ""}
                        <br />
                        {r.harness} / {r.effort}
                        <br />${fmt(r.cost_usd)}
                        {r.estimated_cost_usd === null &&
                          r.known_estimated_cost_usd !== null && (
                            <div className="mt-2">
                              部分估算 ${fmt(r.known_estimated_cost_usd)} ·{" "}
                              {r.requests_missing_usage} 次用量缺失
                            </div>
                          )}
                        {r.estimated_cost_usd !== null && (
                          <div className="mt-2">
                            上界估算 ${fmt(r.estimated_cost_usd)}
                          </div>
                        )}{" "}
                        · {fmt(r.accuracy_pct)}% · {fmt(r.seconds)}s
                      </div>
                    ) : null;
                  }}
                />
                <Scatter data={points} fill="#171717" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="py-12 text-center text-sm text-neutral-500">
            尚无已完成且已结算的真实评测。费用未知时不会绘制为 $0。
          </p>
        )}
      </section>
      <section className="overflow-x-auto rounded-xl border bg-white">
        <table className="w-full whitespace-nowrap text-left text-xs">
          <thead className="border-b bg-neutral-50">
            <tr>
              {[
                "运行 / 模型",
                "评测集 / 指标",
                "Harness / effort",
                "进度",
                "准确率",
                "成本 USD",
                "平均 / P95 耗时",
                "每正确题成本",
                "比较口径",
              ].map((h) => (
                <th className="p-4 font-medium" key={h}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.job_id} className="border-b last:border-0">
                <td className="p-4">
                  <button
                    className="underline underline-offset-4"
                    onClick={() => onRun(r.run_id)}
                  >
                    {r.run_name}
                  </button>
                  <div className="mt-2">{r.model}</div>
                </td>
                <td className="p-4">
                  {r.benchmark}
                  {r.subset ? ` / ${r.subset}` : ""}
                  <div className="mt-2 text-neutral-500">
                    {r.synthetic ? "自检，无模型成绩" : r.metric}
                  </div>
                </td>
                <td className="p-4">
                  {r.harness} / {r.effort}
                  <div className="mt-2 text-neutral-500">
                    effective: {r.effective_effort ?? "未知"}
                  </div>
                </td>
                <td className="p-4">
                  <button
                    className="underline underline-offset-4"
                    onClick={() => setDetail({ run: r.run_id, job: r.job_id })}
                  >
                    {r.completed}/{r.planned} · 逐题
                  </button>
                  <div className="mt-2 text-neutral-500">{r.status}</div>
                </td>
                <td className="p-4">
                  {fmt(r.accuracy === null ? null : r.accuracy * 100, "%")}
                  {r.accuracy === null && r.provisional_accuracy !== null && (
                    <div className="mt-2 text-neutral-500">
                      暂计 {fmt(r.provisional_accuracy * 100)}%
                    </div>
                  )}
                </td>
                <td className="p-4">
                  {fmt(r.cost_usd)}
                  {r.estimated_cost_usd === null &&
                    r.known_estimated_cost_usd !== null && (
                      <div className="mt-2">
                        部分估算 ${fmt(r.known_estimated_cost_usd)} ·{" "}
                        {r.requests_missing_usage} 次用量缺失
                      </div>
                    )}
                  {r.estimated_cost_usd !== null && (
                    <div className="mt-2">
                      上界估算 ${fmt(r.estimated_cost_usd)}
                    </div>
                  )}
                  <div className="mt-2 text-neutral-500">
                    {r.billing_status}
                  </div>
                </td>
                <td className="p-4">
                  {fmt(
                    r.latency_mean_ms === null
                      ? null
                      : r.latency_mean_ms / 1000,
                    "s",
                  )}{" "}
                  /{" "}
                  {fmt(
                    r.latency_p95_ms === null ? null : r.latency_p95_ms / 1000,
                    "s",
                  )}
                  <div className="mt-2 text-neutral-500">
                    {r.latency_samples} 个计时样本
                  </div>
                </td>
                <td className="p-4">{fmt(r.cost_per_correct_usd)}</td>
                <td className="p-4">{r.comparison}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <p className="p-12 text-center text-sm text-neutral-500">
            {result.isLoading ? "正在加载结果…" : "当前筛选条件下没有评测结果"}
          </p>
        )}
      </section>
      {detail && <EpisodeDetails run={detail.run} job={detail.job} />}
      <p className="text-xs leading-6 text-neutral-500">
        CSV 与 JSON 导出使用相同筛选条件，包含 P50/P95、计时样本数、实际 effort
        与结算状态。空值表示未知，不等于零。单题耗时包含容器、Agent
        与评分，排队时间不计；失败也保留计时。自检不参与模型准确率与成本比较；不跨评测集汇总准确率。
      </p>
    </div>
  );
}

function EpisodeDetails({ run, job }: { run: string; job: string }) {
  const data = useQuery({
    queryKey: ["episodes", run],
    queryFn: () =>
      api(
        `/results?level=episodes&run_id=${encodeURIComponent(run)}&include_synthetic=true`,
        z.object({
          rows: z.array(
            z.object({
              job_id: z.string(),
              case_id: z.string(),
              status: z.string(),
              correct: z.boolean().nullable(),
              latency_ms: z.number().nullable(),
              estimated_cost_usd: z.number().nullable(),
              request_count: z.number(),
              error: z.string().nullable(),
            }),
          ),
        }),
      ),
    refetchInterval: 2000,
  });
  return (
    <section className="overflow-x-auto rounded-xl border bg-white p-5">
      <h2 className="mb-4 font-semibold">逐题结果</h2>
      {data.error && <p role="alert">{data.error.message}</p>}
      <table className="w-full text-left text-xs">
        <thead>
          <tr>
            {[
              "题目 ID",
              "状态",
              "正确",
              "耗时 ms",
              "估算 USD",
              "请求数",
              "错误",
            ].map((h) => (
              <th key={h} className="p-2">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.data?.rows
            .filter((r) => r.job_id === job)
            .map((r) => (
              <tr key={r.case_id} className="border-t">
                <td className="p-2">{r.case_id}</td>
                <td>{r.status}</td>
                <td>{r.correct === null ? "—" : r.correct ? "是" : "否"}</td>
                <td>{fmt(r.latency_ms)}</td>
                <td>{fmt(r.estimated_cost_usd)}</td>
                <td>{r.request_count}</td>
                <td>{r.error ?? "—"}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </section>
  );
}
