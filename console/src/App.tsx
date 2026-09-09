import { PriceCap } from "./PriceCap";
import { PrepareEnvironment } from "./PrepareEnvironment";
import { useEffect, useState, type ReactNode, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Boxes,
  Check,
  ChevronRight,
  Circle,
  Cpu,
  FlaskConical,
  LayoutDashboard,
  LoaderCircle,
  Palette,
  Play,
  Plus,
  Settings2,
  ShieldCheck,
  Square,
  Terminal,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import {
  api,
  getBenchmarks,
  getHealth,
  getRuns,
  getTargets,
  runSchema,
  planSchema,
  type Plan,
  targetInput,
  targetSchema,
  type Benchmark,
  type Run,
  type Target,
} from "@/lib/api";
import { Results } from "./Results";
import { useConsole } from "@/lib/store";
import { cn } from "@/lib/utils";

const labels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
  blocked: "未就绪",
};
const money = (v: number | null) =>
  v === null ? "待结算" : `$${v.toFixed(4)}`;
const inputClass =
  "h-11 w-full rounded-md border bg-white px-3 text-sm outline-none focus:border-neutral-500 focus:ring-2 focus:ring-neutral-100";
function Status({ value }: { value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-medium">
      {value === "running" ? (
        <LoaderCircle className="size-3 animate-spin" />
      ) : value === "completed" ? (
        <Check className="size-3" />
      ) : value === "failed" ? (
        <X className="size-3" />
      ) : (
        <Circle className="size-2" />
      )}
      {labels[value] ?? value}
    </span>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="grid gap-2 text-xs font-medium">
      {label}
      {children}
      {hint && (
        <span className="font-normal leading-5 text-muted-foreground">
          {hint}
        </span>
      )}
    </label>
  );
}
function Panel({
  title,
  caption,
  children,
  action,
}: {
  title: string;
  caption?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="min-w-0 overflow-hidden rounded-xl border bg-white">
      <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          {caption && (
            <p className="mt-1 text-xs text-muted-foreground">{caption}</p>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
function Empty({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center px-6 py-10 text-center">
      <div className="mb-4 rounded-xl border bg-muted/50 p-3">
        <FlaskConical className="size-5 text-neutral-400" />
      </div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mb-5 mt-2 max-w-sm text-xs leading-6 text-muted-foreground">
        {detail}
      </p>
      {action}
    </div>
  );
}

export default function App() {
  const client = useQueryClient();
  const { view, setView, selected, select } = useConsole();
  const [modelOpen, setModelOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [error, setError] = useState("");
  const targets = useQuery({ queryKey: ["targets"], queryFn: getTargets });
  const benchmarks = useQuery({
    queryKey: ["benchmarks"],
    queryFn: getBenchmarks,
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: getRuns,
    refetchInterval: 2000,
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 15000,
  });
  useEffect(() => {
    const es = new EventSource("/api/events");
    es.onmessage = () => {
      void client.invalidateQueries({ queryKey: ["runs"] });
    };
    return () => es.close();
  }, [client]);
  const cancel = useMutation({
    mutationFn: (id: string) =>
      api(`/runs/${id}/cancel`, runSchema, { method: "POST", body: "{}" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["runs"] }),
    onError: (e) => setError(e.message),
  });
  const list = runs.data ?? [];
  const active = list.filter(
    (r) => r.status === "running" || r.status === "queued",
  );
  const current =
    list.find((r) => r.id === selected) ??
    active[0] ??
    list.find((r) => r.status === "completed") ??
    list[0];
  const real = list.filter((r) => r.mode === "live");
  const settled = real.flatMap((r) => r.jobs).filter((j) => j.cost !== null);
  const totalCost = settled.length
    ? settled.reduce((s, j) => s + (j.cost ?? 0), 0)
    : null;
  const nav = [
    { id: "overview" as const, label: "总览", icon: LayoutDashboard },
    { id: "results" as const, label: "结果与导出", icon: ArrowDownToLine },
    { id: "runs" as const, label: "评测运行", icon: Activity },
    { id: "models" as const, label: "模型连接", icon: Cpu },
    { id: "benchmarks" as const, label: "评测集", icon: FlaskConical },
    { id: "theme" as const, label: "外观与规范", icon: Palette },
  ];
  return (
    <div className="min-h-screen bg-[#fafafa] lg:grid lg:grid-cols-[218px_minmax(0,1fr)]">
      <aside className="border-b bg-white lg:fixed lg:inset-y-0 lg:w-[218px] lg:border-b-0 lg:border-r">
        <div className="flex h-20 items-center gap-3 px-6">
          <div className="grid size-8 place-items-center rounded-lg bg-black text-white">
            <span className="text-lg font-semibold">λ</span>
          </div>
          <span className="text-xl font-semibold tracking-[-.06em]">
            lyra<span className="ml-1 text-neutral-400">/</span>
          </span>
          <span className="ml-auto rounded border px-1.5 py-0.5 text-[9px] tracking-widest text-muted-foreground">
            LOCAL
          </span>
        </div>
        <div className="hidden px-6 pb-3 pt-5 text-[10px] font-medium tracking-[.18em] text-neutral-400 lg:block">
          EVALUATION WORKSPACE
        </div>
        <nav
          aria-label="主导航"
          className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col"
        >
          {nav.map((n) => (
            <button
              key={n.id}
              onClick={() => setView(n.id)}
              aria-current={view === n.id ? "page" : undefined}
              className={cn(
                "flex min-h-10 shrink-0 items-center gap-3 rounded-lg px-3 text-left text-sm transition-colors",
                view === n.id
                  ? "bg-neutral-100 font-medium text-black"
                  : "text-neutral-500 hover:bg-neutral-50 hover:text-black",
              )}
            >
              <n.icon className="size-4" />
              {n.label}
              {n.id === "runs" && active.length > 0 && (
                <span className="ml-auto rounded bg-white px-1.5 text-xs">
                  {active.length}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="absolute bottom-6 hidden w-full px-5 lg:block">
          <div className="rounded-lg border bg-neutral-50 p-3">
            <div className="flex items-center gap-2 text-xs font-medium">
              <ShieldCheck size={14} />
              本地工作空间
            </div>
            <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
              数据保存在本机
              <br />
              模型请求经 APIGO Gateway
            </p>
          </div>
          <p className="mt-4 text-[10px] text-neutral-400">LYRA EVALS · 0.1</p>
        </div>
      </aside>
      <div className="min-w-0 lg:col-start-2">
        <header className="flex h-16 items-center justify-between border-b bg-white/90 px-5 md:px-9">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            工作空间
            <ChevronRight size={12} />
            <span className="text-foreground">
              {nav.find((n) => n.id === view)?.label}
            </span>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-neutral-500">
            <span className="hidden sm:inline">Claude Agent · ACP</span>
            <span className="h-3 border-l" />
            <span className="flex items-center gap-1.5">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  health.data?.docker ? "bg-black" : "bg-neutral-300",
                )}
              />
              Docker {health.data?.docker ? "已连接" : "未连接"}
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-[1560px] px-5 py-7 md:px-9 md:py-9">
          <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="mb-2 text-[10px] font-medium tracking-[.2em] text-neutral-400">
                {view === "theme" ? "DESIGN LANGUAGE" : "MEASURE WHAT MATTERS"}
              </div>
              <h1 className="text-2xl font-semibold tracking-tight md:text-[28px]">
                {
                  {
                    overview: "每一次比较，都有据可循。",
                    results: "成本、时间与质量，一览可见。",
                    runs: "评测运行",
                    models: "连接你的模型",
                    benchmarks: "五个维度，理解模型能力。",
                    theme: "安静的界面，清晰的数据。",
                  }[view]
                }
              </h1>
              <p className="mt-3 text-sm leading-6 text-muted-foreground">
                {
                  {
                    overview: "在相同任务与执行条件下，观察质量、成本和时间。",
                    results: "查看各模型变体的评测成绩，导出可复核的数据。",
                    runs: "追踪并行作业，查看逐题进度与执行证据。",
                    models: "配置 APIGO 入口。API Key 只保存在本地服务端。",
                    benchmarks:
                      "官方任务与评分；GPT 使用 Codex，Claude 使用 Claude Agent。",
                    theme: "Paper / Ink · 以白色承载内容，以黑色建立重点。",
                  }[view]
                }
              </p>
            </div>
            <Button
              onClick={() =>
                view === "models" ? setModelOpen(true) : setRunOpen(true)
              }
            >
              <Plus />
              {view === "models" ? "添加模型" : "新建评测"}
            </Button>
          </div>
          {(error || runs.error || targets.error) && (
            <div
              role="alert"
              className="mb-5 flex items-center justify-between rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
            >
              {error || (runs.error ?? targets.error)?.message}
              <button aria-label="关闭提示" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {view === "overview" && (
            <>
              <div className="mb-6 grid grid-cols-2 gap-3 xl:grid-cols-4">
                {[
                  {
                    label: "正在运行",
                    value: String(active.length).padStart(2, "0"),
                    unit: "个实验",
                    sub: `${active.flatMap((r) => r.jobs).filter((j) => j.status === "running").length} 项并行作业`,
                    icon: Activity,
                  },
                  {
                    label: "已连接模型",
                    value: String(targets.data?.length ?? 0).padStart(2, "0"),
                    unit: "个目标",
                    sub: "独立 endpoint / model / protocol",
                    icon: Cpu,
                  },
                  {
                    label: "评测集",
                    value: "05",
                    unit: "个赛道",
                    sub: "指令 · 推理 · 代码 · 工具 · 工程",
                    icon: FlaskConical,
                  },
                  {
                    label: "真实评测支出",
                    value: settled.length ? money(totalCost) : "—",
                    unit: "USD",
                    sub: settled.length ? "仅计已结算费用" : "暂无已结算记录",
                    icon: Zap,
                  },
                ].map((s) => (
                  <div key={s.label} className="rounded-xl border bg-white p-5">
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      {s.label}
                      <s.icon size={15} />
                    </div>
                    <div className="my-4 flex items-baseline gap-2">
                      <span className="text-3xl font-medium tracking-tight tabular-nums">
                        {s.value}
                      </span>
                      <span className="text-[11px] text-neutral-400">
                        {s.unit}
                      </span>
                    </div>
                    <p className="text-[10px] leading-5 text-muted-foreground">
                      {s.sub}
                    </p>
                  </div>
                ))}
              </div>
              <div className="grid gap-5 xl:grid-cols-[minmax(0,1.8fr)_minmax(280px,1fr)]">
                <Panel
                  title="执行脉搏"
                  caption={
                    current
                      ? `${current.name} · ${current.mode === "smoke" ? "沙箱自检，不代表模型成绩" : "真实评测"}`
                      : "完成首个运行后，逐题事件将在这里汇成曲线"
                  }
                  action={<Activity size={15} className="text-neutral-400" />}
                >
                  {current ? (
                    <ProgressChart run={current} />
                  ) : (
                    <Empty
                      title="让第一条曲线开始生长"
                      detail="添加模型并创建评测，或先运行不调用模型的 Docker / ACP 自检。"
                      action={
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setRunOpen(true)}
                        >
                          创建第一个运行
                          <ArrowRight />
                        </Button>
                      }
                    />
                  )}
                </Panel>
                <Panel title="运行环境" caption="正式执行前检查所有边界">
                  <div className="space-y-5 p-5">
                    {[
                      [
                        "Docker Engine",
                        health.data?.docker ? "可用" : "未连接",
                      ],
                      ["自检镜像", health.data?.image ? "已构建" : "待构建"],
                      [
                        "Agent 镜像",
                        `Claude ${health.data?.acp_image ? "✓" : "—"} · Codex ${health.data?.codex_image ? "✓" : "—"}`,
                      ],
                      [
                        "正式执行门禁",
                        health.data?.live_enabled ? "已启用" : "未启用",
                      ],
                    ].map(([k, v]) => (
                      <div
                        key={k}
                        className="flex items-center justify-between text-xs"
                      >
                        <span className="text-muted-foreground">{k}</span>
                        <span className="flex items-center gap-1.5">
                          <Circle size={7} />
                          {v}
                        </span>
                      </div>
                    ))}
                    <div className="border-t pt-4 text-[11px] leading-5 text-muted-foreground">
                      {health.data?.reason ?? "正在连接本地控制器…"}
                    </div>
                  </div>
                </Panel>
              </div>
              <div className="mt-6">
                <RunTable
                  runs={list.slice(0, 5)}
                  onSelect={(r) => {
                    select(r.id);
                    setView("runs");
                  }}
                  onNew={() => setRunOpen(true)}
                />
              </div>
            </>
          )}
          {view === "results" && (
            <Results
              onRun={(id) => {
                select(id);
                setView("runs");
              }}
            />
          )}
          {view === "runs" && (
            <div className="space-y-5">
              <RunTable
                runs={list}
                onSelect={(r) => select(r.id)}
                onNew={() => setRunOpen(true)}
              />
              {current && (
                <RunDetail
                  run={current}
                  onCancel={() => cancel.mutate(current.id)}
                  pending={cancel.isPending}
                />
              )}
            </div>
          )}
          {view === "models" && (
            <ModelList
              models={targets.data ?? []}
              onAdd={() => setModelOpen(true)}
              onError={setError}
            />
          )}
          {view === "benchmarks" && (
            <div className="grid gap-4 md:grid-cols-2">
              {(benchmarks.data ?? []).map((b, i) => (
                <section key={b.id} className="rounded-xl border bg-white p-6">
                  <div className="flex items-start justify-between">
                    <span className="text-[10px] tracking-widest text-neutral-400">
                      0{i + 1} / BENCHMARK
                    </span>
                    <Status value={b.live_ready ? "已就绪" : b.status} />
                  </div>
                  <h2 className="mt-6 text-xl font-semibold">{b.name}</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    {b.description}
                  </p>
                  <div className="mt-6 flex gap-5 border-y py-3 text-xs">
                    <span>{b.cases ?? "待冻结"} 题</span>
                    <span className="text-muted-foreground">{b.tools}</span>
                  </div>
                  <p className="mt-4 text-xs leading-6 text-muted-foreground">
                    {b.reason}
                  </p>
                </section>
              ))}
            </div>
          )}
          {view === "theme" && <Theme />}
          <footer className="mt-10 flex flex-wrap justify-between gap-2 border-t pt-5 text-[10px] text-neutral-400">
            <span>本地运行 · 证据优先 · 未结算费用不记为零</span>
            <span>LYRA / EVALUATION CONSOLE</span>
          </footer>
        </main>
      </div>
      <ModelDialog open={modelOpen} close={() => setModelOpen(false)} />
      <RunDialog
        open={runOpen}
        close={() => setRunOpen(false)}
        models={targets.data ?? []}
        benchmarks={benchmarks.data ?? []}
        onCreated={(r) => {
          select(r.id);
          setView("runs");
        }}
      />
    </div>
  );
}
function ProgressChart({ run }: { run: Run }) {
  const points = run.events
    .filter((e) => e.message.startsWith("完成题目"))
    .map((e, i) => ({
      step: i + 1,
      count: i + 1,
      time: new Date(e.at).toLocaleTimeString(),
    }));
  if (!points.length)
    return (
      <Empty
        title="等待首个题目完成"
        detail="进度来自持久化事件。取消、失败和评分阻塞也会保留在运行记录中。"
      />
    );
  return (
    <div className="p-5">
      <div className="mb-4 flex justify-between text-xs text-muted-foreground">
        <span>累计完成题数</span>
        <span className="tabular-nums">
          {points.length} / {run.jobs.reduce((s, j) => s + j.total, 0)}
        </span>
      </div>
      <div
        className="h-52 w-full"
        role="img"
        aria-label={`累计完成 ${points.length} 题`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={[{ step: 0, count: 0, time: "开始" }, ...points]}
            margin={{ left: -25, right: 6, top: 8, bottom: 0 }}
          >
            <CartesianGrid stroke="#eeeeee" vertical={false} />
            <XAxis
              dataKey="step"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "#999" }}
            />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "#999" }}
            />
            <Tooltip
              contentStyle={{
                borderRadius: 10,
                border: "1px solid #eee",
                fontSize: 12,
              }}
              labelFormatter={(v) => `完成序号 ${v}`}
              formatter={(v) => [v, "完成题数"]}
            />
            <Area
              type="stepAfter"
              dataKey="count"
              stroke="#171717"
              strokeWidth={2}
              fill="#f2f2f2"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
function RunTable({
  runs,
  onSelect,
  onNew,
}: {
  runs: Run[];
  onSelect: (r: Run) => void;
  onNew: () => void;
}) {
  return (
    <Panel
      title="评测记录"
      caption="每次运行保存独立计划与执行证据"
      action={
        <span className="text-xs text-neutral-400">{runs.length} 个运行</span>
      }
    >
      {!runs.length ? (
        <Empty
          title="还没有评测记录"
          detail="选择评测集与模型，设置并发与预算，即可创建运行。"
          action={
            <Button variant="outline" size="sm" onClick={onNew}>
              <Plus />
              新建评测
            </Button>
          }
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="bg-neutral-50 text-[10px] font-normal text-muted-foreground">
              <tr>
                {["实验名称", "类型", "进度", "状态", "创建时间", ""].map(
                  (h, i) => (
                    <th key={i} className="px-5 py-3 font-medium">
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const done = r.jobs.reduce((s, j) => s + j.completed, 0),
                  total = r.jobs.reduce((s, j) => s + j.total, 0);
                return (
                  <tr key={r.id} className="border-t hover:bg-neutral-50/70">
                    <td className="px-5 py-4">
                      <button
                        className="text-left font-medium hover:underline"
                        onClick={() => onSelect(r)}
                      >
                        {r.name}
                      </button>
                      <div className="mt-1 font-mono text-[10px] text-neutral-400">
                        {r.id.slice(0, 8)}
                      </div>
                    </td>
                    <td className="px-5">
                      {r.mode === "smoke" ? "沙箱自检" : "真实评测"}
                    </td>
                    <td className="px-5 tabular-nums">
                      {done} / {total}
                    </td>
                    <td className="px-5">
                      <Status value={r.status} />
                    </td>
                    <td className="whitespace-nowrap px-5 text-muted-foreground">
                      {new Date(r.created_at).toLocaleString("zh-CN", {
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </td>
                    <td className="px-5">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`查看 ${r.name}`}
                        onClick={() => onSelect(r)}
                      >
                        <ChevronRight />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
function RunDetail({
  run,
  onCancel,
  pending,
}: {
  run: Run;
  onCancel: () => void;
  pending: boolean;
}) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{run.name}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            作业并发 {run.max_jobs} · 每作业题目并发 {run.per_job} · 容器总上限{" "}
            {run.max_episodes}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const blob = new Blob([JSON.stringify(run, null, 2)], {
                type: "application/json",
              });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `lyra-${run.id}.json`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            <ArrowDownToLine />
            导出证据
          </Button>
          {["queued", "running"].includes(run.status) && (
            <Button
              variant="outline"
              size="sm"
              onClick={onCancel}
              disabled={pending}
            >
              <Square />
              取消运行
            </Button>
          )}
        </div>
      </div>
      {run.mode === "smoke" && (
        <div className="rounded-lg border border-dashed bg-neutral-50 p-3 text-xs leading-6">
          这是 Docker / ACP 合成任务自检。完成率仅表示调度链路通过，不是
          benchmark 分数，也没有调用模型。
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {run.jobs.map((j) => (
          <section key={j.id} className="rounded-xl border bg-white p-5">
            <div className="flex justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">{j.benchmark}</h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {j.target_name}
                </p>
              </div>
              <Status value={j.status} />
            </div>
            <progress
              className="my-5 h-1.5 w-full overflow-hidden rounded-full accent-black"
              value={j.completed}
              max={j.total || 1}
            />
            <div className="grid grid-cols-3 gap-2 text-xs">
              <span>
                <span className="block pb-2 text-[10px] text-muted-foreground">
                  完成
                </span>
                {j.completed} / {j.total}
              </span>
              <span>
                <span className="block pb-2 text-[10px] text-muted-foreground">
                  {run.mode === "smoke" ? "自检通过" : "整题正确"}
                </span>
                {j.correct}
              </span>
              <span>
                <span className="block pb-2 text-[10px] text-muted-foreground">
                  {run.mode === "smoke" ? "模型费用" : "已结算费用"}
                </span>
                {money(j.cost)}
              </span>
            </div>
            {j.error && (
              <p className="mt-4 break-words rounded bg-red-50 p-3 text-xs leading-5 text-red-800">
                {j.error}
              </p>
            )}
          </section>
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="执行进度" caption="来自已持久化的完成事件">
          <ProgressChart run={run} />
        </Panel>
        <Panel title="事件时间线" caption="执行状态与恢复证据">
          <ol
            className="max-h-80 space-y-4 overflow-y-auto p-5"
            aria-live="polite"
          >
            {run.events
              .slice(-40)
              .reverse()
              .map((e) => (
                <li key={e.seq} className="flex gap-3 text-xs">
                  <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-neutral-300" />
                  <time className="shrink-0 font-mono text-[10px] text-neutral-400">
                    {new Date(e.at).toLocaleTimeString()}
                  </time>
                  <span className="break-words leading-5">{e.message}</span>
                </li>
              ))}
          </ol>
        </Panel>
      </div>
    </div>
  );
}
function ModelList({
  models,
  onAdd,
  onError,
}: {
  models: Target[];
  onAdd: () => void;
  onError: (s: string) => void;
}) {
  const client = useQueryClient();
  const del = useMutation({
    mutationFn: (id: string) =>
      api(`/targets/${id}`, z.object({ ok: z.boolean() }), {
        method: "DELETE",
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["targets"] }),
    onError: (e) => onError(e.message),
  });
  return (
    <Panel
      title="模型连接"
      caption="GPT 使用 Codex / Responses；Claude 使用 Claude Agent / Messages"
    >
      {models.length ? (
        <div className="divide-y">
          {models.map((m) => (
            <div key={m.id} className="flex flex-wrap items-center gap-4 p-5">
              <div className="rounded-lg border p-3">
                <Cpu size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-medium">{m.name}</h2>
                <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                  {m.model} · {m.endpoint}
                </p>
                <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-neutral-500">
                  <span>{m.protocol}</span>
                  <span>凭据已保存</span>
                  <span>能力以冻结计划的执行预检为准</span>
                </div>
                <PriceCap targetId={m.id} />
              </div>
              <Button
                variant="ghost"
                size="icon"
                disabled={del.isPending}
                aria-label={`删除 ${m.name}`}
                onClick={() => {
                  if (
                    window.confirm(
                      `删除模型配置「${m.name}」？历史实验快照仍保留。`,
                    )
                  )
                    del.mutate(m.id);
                }}
              >
                <Trash2 />
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="从一个模型开始"
          detail="填写 APIGO endpoint、模型名称、协议和专用 API Key。服务端不会回传完整凭据。"
          action={
            <Button size="sm" variant="outline" onClick={onAdd}>
              <Plus />
              添加模型
            </Button>
          }
        />
      )}
    </Panel>
  );
}
function ModelDialog({ open, close }: { open: boolean; close: () => void }) {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const raw = Object.fromEntries(new FormData(form));
    const parsed = targetInput.safeParse(raw);
    if (!parsed.success) {
      setError(parsed.error.issues[0].message);
      return;
    }
    setBusy(true);
    try {
      await api("/targets", targetSchema, {
        method: "POST",
        body: JSON.stringify(parsed.data),
      });
      form.reset();
      await client.invalidateQueries({ queryKey: ["targets"] });
      setError("");
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) close();
      }}
      title="添加模型连接"
      description="连接 APIGO Gateway。凭据不会进入浏览器持久化存储或运行报告。"
    >
      <form className="mt-6 grid gap-4" onSubmit={submit}>
        <Field label="配置名称">
          <input
            className={inputClass}
            name="name"
            placeholder="例如 Fusion · Balanced"
            required
            maxLength={80}
          />
        </Field>
        <Field
          label="Gateway Endpoint"
          hint="填写基础地址，不包含 /messages 或 /chat/completions 路径。"
        >
          <input
            className={inputClass}
            name="endpoint"
            type="url"
            placeholder="https://gateway.example.com"
            required
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="模型名称">
            <input
              className={inputClass}
              name="model"
              placeholder="Gateway 中的模型 ID"
              required
            />
          </Field>
          <Field label="协议">
            <select
              className={inputClass}
              name="protocol"
              defaultValue="anthropic_messages"
            >
              <option value="anthropic_messages">Anthropic Messages</option>
              <option value="openai_chat_completions">
                OpenAI Chat Completions
              </option>
              <option value="openai_responses">OpenAI Responses</option>
            </select>
          </Field>
        </div>
        <Field
          label="API Key"
          hint="使用评测专用、受预算限制的凭据。本机私有文件保存。"
        >
          <input
            className={inputClass}
            name="api_key"
            type="password"
            autoComplete="new-password"
            placeholder="输入 API Key"
            required
          />
        </Field>
        {error && (
          <p role="alert" className="text-xs text-red-700">
            {error}
          </p>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={close}>
            取消
          </Button>
          <Button disabled={busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Check />}
            保存连接
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
function RunDialog({
  open,
  close,
  models,
  benchmarks,
  onCreated,
}: {
  open: boolean;
  close: () => void;
  models: Target[];
  benchmarks: Benchmark[];
  onCreated: (r: Run) => void;
}) {
  const client = useQueryClient();
  const [mode, setMode] = useState("smoke");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const savedPlans = useQuery({
    queryKey: ["plans"],
    queryFn: () => api("/plans", z.array(planSchema)),
    enabled: open && mode === "live",
  });
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const payload = {
      name: f.get("name"),
      mode,
      benchmarks: f.getAll("benchmarks"),
      targets: mode === "smoke" ? [] : f.getAll("targets"),
      sample_size: Number(f.get("sample_size")),
      trials: Number(f.get("trials")),
      max_jobs: Number(f.get("max_jobs")),
      per_job: Number(f.get("per_job")),
      max_episodes: Number(f.get("max_episodes")),
      budget: Number(f.get("budget") || 1),
      confirm_budget: f.get("confirm") === "on",
    };
    setBusy(true);
    try {
      if (mode === "live") {
        const selected = models.filter((m) =>
          f.getAll("targets").includes(m.id),
        );
        const result = await api("/plans", planSchema, {
          method: "POST",
          body: JSON.stringify({
            name: payload.name,
            benchmarks: payload.benchmarks,
            sample_size: payload.sample_size,
            trials: payload.trials,
            max_jobs: payload.max_jobs,
            per_job: payload.per_job,
            max_episodes: payload.max_episodes,
            budget: String(payload.budget),
            seed: Number(f.get("seed")),
            tau_simulator_target_id: f.get("tau_simulator") || null,
            tau_simulator_budget: f.get("tau_simulator_budget") || "0.5",
            budget_policy: f.get("budget_policy"),
            variants: selected.map((m) => ({
              target_id: m.id,
              harness: f.get(`harness_${m.id}`),
              efforts: f.getAll(`effort_${m.id}`),
              max_output_tokens: Number(f.get("max_output_tokens")),
              deadline_seconds: Number(f.get("deadline_seconds")),
              episode_budget: f.get("episode_budget"),
            })),
          }),
        });
        setPlan(result);
        await client.invalidateQueries({ queryKey: ["plans"] });
        setError("");
        return;
      }
      const r = await api("/runs", runSchema, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await client.invalidateQueries({ queryKey: ["runs"] });
      setError("");
      close();
      onCreated(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) close();
      }}
      title="新建评测"
      description="冻结任务、目标和并发计划。每道题获得独立 Docker / ACP 会话。"
    >
      <form
        onSubmit={submit}
        onChange={(event) => {
          if (
            event.target instanceof HTMLElement &&
            event.target.getAttribute("name")
          )
            setPlan(null);
        }}
        className="mt-5 grid gap-4"
      >
        <Field label="实验名称">
          <input
            className={inputClass}
            name="name"
            defaultValue="我的第一次评测"
            required
            maxLength={100}
          />
        </Field>
        <div className="grid grid-cols-2 gap-2 rounded-lg bg-muted p-1">
          {[
            ["smoke", "沙箱自检 · 不调用模型"],
            ["live", "真实模型评测"],
          ].map(([v, t]) => (
            <button
              key={v}
              type="button"
              className={cn(
                "rounded-md px-2 py-3 text-xs",
                mode === v
                  ? "bg-white font-medium shadow-sm"
                  : "text-muted-foreground",
              )}
              onClick={() => {
                setMode(v);
                setPlan(null);
                setError("");
              }}
            >
              {t}
            </button>
          ))}
        </div>
        <fieldset>
          <legend className="mb-2 text-xs font-medium">选择评测集</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {benchmarks.map((b) => (
              <label
                key={b.id}
                className="flex items-center gap-2 rounded-lg border p-3 text-xs"
              >
                <input
                  type="checkbox"
                  className="accent-black"
                  name="benchmarks"
                  value={b.id}
                  defaultChecked={b.id === "ifeval"}
                />
                {b.name}
              </label>
            ))}
          </div>
        </fieldset>
        {mode === "smoke" ? (
          <p className="rounded-lg bg-muted p-3 text-xs leading-5 text-muted-foreground">
            仅验证 Docker、ACP
            和并发调度。各评测集运行相同的合成握手任务，不产生模型成绩。
          </p>
        ) : (
          <fieldset>
            <legend className="mb-2 text-xs font-medium">选择模型</legend>
            <div className="mb-4 grid gap-3 rounded border p-3 sm:grid-cols-2">
              <Field label="τ² 固定用户模拟器 / Judge">
                <select
                  name="tau_simulator"
                  className={inputClass}
                  defaultValue={
                    models.find((m) => m.model === "gpt-5.6-luna")?.id ?? ""
                  }
                >
                  <option value="">仅 τ² 需要选择</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="τ² 每题模拟器预算 USD">
                <input
                  name="tau_simulator_budget"
                  className={inputClass}
                  type="number"
                  min="0.01"
                  step="0.01"
                  defaultValue="0.5"
                />
              </Field>
            </div>
            {models.length ? (
              models.map((m) => (
                <div
                  key={m.id}
                  className="mb-2 space-y-3 rounded border p-3 text-xs"
                >
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      name="targets"
                      value={m.id}
                      className="accent-black"
                    />
                    {m.name}
                    <span className="ml-auto text-neutral-400">
                      {m.protocol}
                    </span>
                  </label>
                  <select
                    name={`harness_${m.id}`}
                    className={inputClass}
                    defaultValue={
                      m.model.startsWith("gpt-") ||
                      m.protocol === "openai_responses"
                        ? "codex"
                        : "claude_agent"
                    }
                  >
                    <option
                      value="codex"
                      disabled={
                        m.protocol !== "openai_responses" ||
                        m.model.startsWith("claude-")
                      }
                    >
                      Codex ACP · Responses
                    </option>
                    <option
                      value="claude_agent"
                      disabled={
                        m.protocol !== "anthropic_messages" ||
                        m.model.startsWith("gpt-")
                      }
                    >
                      Claude Agent ACP · Messages
                    </option>
                  </select>
                  <div className="flex flex-wrap gap-3">
                    {[
                      "provider_default",
                      "low",
                      "medium",
                      "high",
                      m.protocol === "openai_responses" ? "xhigh" : "max",
                    ].map((effort) => (
                      <label key={effort} className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          name={`effort_${m.id}`}
                          value={effort}
                          defaultChecked={effort === "provider_default"}
                        />
                        {effort === "provider_default" ? "提供商默认" : effort}
                      </label>
                    ))}
                  </div>
                  <p className="text-muted-foreground">
                    候选参数 · 未预检，不代表该模型支持这些档位
                  </p>
                </div>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">
                请先在模型连接中添加目标。
              </p>
            )}
          </fieldset>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="每个作业题数">
            <input
              className={inputClass}
              type="number"
              name="sample_size"
              defaultValue={3}
              min={1}
              max={mode === "live" ? 10000 : 20}
              required
            />
          </Field>
          <Field label="重复次数">
            <input
              className={inputClass}
              type="number"
              name="trials"
              defaultValue={1}
              min={1}
              max={10}
              required
            />
          </Field>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <Field label="并行作业">
            <input
              className={inputClass}
              type="number"
              name="max_jobs"
              defaultValue={2}
              min={1}
              max={8}
              required
            />
          </Field>
          <Field label="每作业并发">
            <input
              className={inputClass}
              type="number"
              name="per_job"
              defaultValue={2}
              min={1}
              max={8}
              required
            />
          </Field>
          <Field label="容器总上限">
            <input
              className={inputClass}
              type="number"
              name="max_episodes"
              defaultValue={4}
              min={1}
              max={16}
              required
            />
          </Field>
        </div>
        {mode === "live" && (
          <>
            <Field label="实验预算上限 / USD">
              <input
                className={inputClass}
                type="number"
                name="budget"
                min={0.01}
                step={0.01}
                defaultValue={5}
                required
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="比较预算策略">
                <select className={inputClass} name="budget_policy">
                  <option value="capability">能力扫描</option>
                  <option value="equal_cost">相同单题费用上限</option>
                  <option value="equal_time">相同任务时间上限</option>
                </select>
              </Field>
              <Field label="随机种子">
                <input
                  className={inputClass}
                  type="number"
                  name="seed"
                  defaultValue={0}
                  min={0}
                  required
                />
              </Field>
              <Field label="单题费用上限 / USD">
                <input
                  className={inputClass}
                  type="number"
                  name="episode_budget"
                  defaultValue={1}
                  min={0.01}
                  step={0.01}
                  required
                />
              </Field>
              <Field label="任务时限 / 秒">
                <input
                  className={inputClass}
                  type="number"
                  name="deadline_seconds"
                  defaultValue={600}
                  min={1}
                  max={86400}
                  required
                />
              </Field>
              <Field label="输出 token 上限">
                <input
                  className={inputClass}
                  type="number"
                  name="max_output_tokens"
                  defaultValue={16384}
                  min={1}
                  max={1000000}
                  required
                />
              </Field>
            </div>
            <p className="text-xs text-muted-foreground">
              仅保存离线计划，不发起调用。费用上限不是报价或预留保障。
            </p>
            <label className="hidden">
              <input
                className="mt-1 accent-black"
                name="confirm"
                type="checkbox"
              />
              确认上述真实调用预算。未满足数据、计费或隔离门禁时，服务端会拒绝执行。
            </label>
          </>
        )}
        {mode === "live" && (savedPlans.data?.length ?? 0) > 0 && (
          <Field label="查看已保存计划（只读，不覆盖当前表单）">
            <select
              className={inputClass}
              value=""
              onChange={(event) =>
                setPlan(
                  savedPlans.data?.find(
                    (item) => item.id === event.target.value,
                  ) ?? null,
                )
              }
            >
              <option value="">选择历史计划</option>
              {savedPlans.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id.slice(0, 12)} · {item.episodes} 个任务
                </option>
              ))}
            </select>
          </Field>
        )}
        {plan && (
          <section
            className="space-y-3 rounded-xl border p-4 text-xs"
            aria-live="polite"
          >
            <h3 className="font-medium">
              计划已保存 · {plan.manifest.variants.length} 个变体 / {plan.jobs}{" "}
              个作业 / {plan.episodes} 个任务
            </h3>
            <p>
              费用估算：{plan.estimated_cost ?? "未知"} · 已预留：$
              {plan.reserved_cost} · 实验预算：${plan.budget}
            </p>
            <p>所有单题上限合计：${plan.episode_caps_total}（不是费用预测）</p>
            <div className="max-h-48 overflow-auto">
              <table className="w-full text-left">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>Harness</th>
                    <th>Effort</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.manifest.variants.map((v) => (
                    <tr key={v.id} className="border-t">
                      <td className="py-2">{v.connection.model}</td>
                      <td>{v.harness}</td>
                      <td>{v.effort} · 未验证</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {plan.manifest.benchmarks.includes("swebench") && (
              <PrepareEnvironment planId={plan.id} />
            )}
            <p>比较口径：{plan.manifest.comparison}；实际 effort 未知</p>
            <ul className="list-inside list-disc leading-6">
              {plan.blockers.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
            <p className="break-all text-muted-foreground">
              配置哈希：{plan.id}
            </p>
            <Button
              type="button"
              variant="outline"
              onClick={async () => {
                const response = await api(
                  `/plans/${plan.id}`,
                  z.record(z.string(), z.unknown()),
                );
                const url = URL.createObjectURL(
                  new Blob([JSON.stringify(response, null, 2)], {
                    type: "application/json",
                  }),
                );
                const link = document.createElement("a");
                link.href = url;
                link.download = `plan-${plan.id.slice(0, 12)}.json`;
                link.click();
                URL.revokeObjectURL(url);
              }}
            >
              导出完整计划
            </Button>
            <Button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  const frozen = await api(
                    `/plans/${plan.id}`,
                    z.object({
                      manifest: z.object({
                        name: z.string(),
                        benchmarks: z.array(z.string()),
                        sample_size: z.number(),
                        trials: z.number(),
                        max_jobs: z.number(),
                        per_job: z.number(),
                        max_episodes: z.number(),
                        variants: z.array(z.object({ target_id: z.string() })),
                      }),
                    }),
                  );
                  const m = frozen.manifest;
                  const run = await api("/runs", runSchema, {
                    method: "POST",
                    body: JSON.stringify({
                      name: m.name,
                      mode: "live",
                      benchmarks: m.benchmarks,
                      targets: [...new Set(m.variants.map((v) => v.target_id))],
                      sample_size: m.sample_size,
                      trials: m.trials,
                      max_jobs: m.max_jobs,
                      per_job: m.per_job,
                      max_episodes: m.max_episodes,
                      budget: Number(plan.budget),
                      confirm_budget: true,
                      plan_id: plan.id,
                    }),
                  });
                  await client.invalidateQueries({ queryKey: ["runs"] });
                  close();
                  onCreated(run);
                } catch (e) {
                  setError(e instanceof Error ? e.message : "执行失败");
                } finally {
                  setBusy(false);
                }
              }}
            >
              授权最多 ${plan.budget} 并运行验收
            </Button>
            <p className="text-muted-foreground">
              五个赛道均有执行适配。τ² 需固定模拟器；SWE
              请先准备所选实例环境。执行前冻结镜像与价格上界，预留合计不得超过授权预算。账单待结算。
            </p>
          </section>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-lg bg-red-50 p-3 text-xs leading-5 text-red-800"
          >
            {error}
          </p>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={close}>
            取消
          </Button>
          <Button disabled={busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Play />}
            {mode === "live" ? "生成并保存计划" : "创建并运行"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
function Theme() {
  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Paper / Ink" caption="白色主题 · 黑色强调 · 语义化状态">
          <div className="grid grid-cols-4 gap-3 p-5">
            {[
              ["Paper", "#ffffff"],
              ["Canvas", "#fafafa"],
              ["Muted", "#737373"],
              ["Ink", "#171717"],
            ].map(([n, c]) => (
              <div key={n}>
                <div
                  className="h-20 rounded-lg border"
                  style={{ background: c }}
                />
                <p className="mt-3 text-xs font-medium">{n}</p>
                <p className="mt-1 font-mono text-[10px] text-neutral-400">
                  {c}
                </p>
              </div>
            ))}
          </div>
        </Panel>
        <Panel
          title="层级与排版"
          caption="系统字体，内容优先，数字采用等宽间距"
        >
          <div className="space-y-4 p-5">
            <p className="text-3xl font-semibold tracking-tight">
              看见模型的真实表现。
            </p>
            <p className="text-sm leading-6 text-muted-foreground">
              减少装饰，让任务、数据和关键操作保持清晰。正文、辅助说明、状态分别建立稳定的视觉层级。
            </p>
            <p className="text-2xl font-medium tabular-nums">
              98.42 <span className="text-xs text-neutral-400">/ 100</span>
            </p>
            <p className="text-[10px] text-neutral-400">
              以上数字仅为排版样本，不是评测结果
            </p>
          </div>
        </Panel>
      </div>
      <Panel title="控件与状态" caption="状态同时使用文字与图形，不仅依赖颜色">
        <div className="flex flex-wrap items-center gap-3 p-5">
          <Button>
            <Play />
            开始评测
          </Button>
          <Button variant="outline">查看报告</Button>
          <Button variant="ghost">次要操作</Button>
          <Button disabled>等待就绪</Button>
          <Status value="running" />
          <Status value="completed" />
          <Status value="failed" />
        </div>
      </Panel>
      <Panel
        title="交互原则"
        caption="参考 Apple HIG，按本地 Web Console 场景适配"
      >
        <div className="grid gap-6 p-5 md:grid-cols-3">
          {[
            [
              Boxes,
              "有序并发",
              "全局容器上限始终可见，作业与题目进度分层呈现。",
            ],
            [
              ShieldCheck,
              "明确反馈",
              "未结算显示待结算，自检与真实成绩分开，不用动画掩盖执行状态。",
            ],
            [
              Settings2,
              "可访问操作",
              "键盘焦点、表单标签、减少动态效果；图表提供文字和可导出证据。",
            ],
          ].map(([Icon, title, desc]) => {
            const I = Icon as typeof Boxes;
            return (
              <div key={String(title)}>
                <I size={20} />
                <h3 className="mt-3 text-sm font-medium">{String(title)}</h3>
                <p className="mt-2 text-xs leading-6 text-muted-foreground">
                  {String(desc)}
                </p>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}
