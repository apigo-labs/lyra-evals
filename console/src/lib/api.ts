import { z } from "zod";
export const targetInput = z.object({
  name: z.string().min(1, "请输入配置名称"),
  endpoint: z
    .url("请输入有效 URL")
    .refine(
      (s) => !new URL(s).username && !new URL(s).password,
      "地址不能包含凭据",
    ),
  model: z.string().min(1, "请输入模型名称"),
  protocol: z.enum([
    "anthropic_messages",
    "openai_chat_completions",
    "openai_responses",
  ]),
  api_key: z.string().min(1, "请输入 API Key"),
});
export const targetSchema = targetInput
  .omit({ api_key: true })
  .extend({ id: z.string(), has_key: z.boolean() });
export type Target = z.infer<typeof targetSchema>;
export const benchmarkSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  status: z.string(),
  reason: z.string(),
  cases: z.number().nullable(),
  tools: z.string(),
  live_ready: z.boolean(),
});
export type Benchmark = z.infer<typeof benchmarkSchema>;
export const jobSchema = z.object({
  id: z.string(),
  benchmark: z.string(),
  target_name: z.string(),
  status: z.string(),
  completed: z.number(),
  total: z.number(),
  correct: z.number(),
  cost: z.number().nullable(),
  latencies: z.array(z.number()),
  error: z.string().nullable(),
});
export const runSchema = z.object({
  id: z.string(),
  name: z.string(),
  mode: z.string(),
  status: z.string(),
  created_at: z.string(),
  max_jobs: z.number(),
  max_episodes: z.number(),
  per_job: z.number(),
  budget: z.number(),
  jobs: z.array(jobSchema),
  events: z.array(
    z.object({
      seq: z.number(),
      at: z.string(),
      message: z.string(),
      job_id: z.string().nullable(),
    }),
  ),
  manifest: z.record(z.string(), z.unknown()),
});
export type Run = z.infer<typeof runSchema>;
export const healthSchema = z.object({
  docker: z.boolean(),
  image: z.boolean(),
  acp_image: z.boolean(),
  codex_image: z.boolean().default(false),
  live_enabled: z.boolean(),
  version: z.string(),
  reason: z.string(),
});
export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Lyra-Console": "1",
      ...init?.headers,
    },
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: "服务暂不可用" }));
    throw new Error(typeof e.detail === "string" ? e.detail : "配置格式不正确");
  }
  return schema.parse(await r.json());
}
export const getTargets = () => api("/targets", z.array(targetSchema));
export const getBenchmarks = () => api("/benchmarks", z.array(benchmarkSchema));
export const getRuns = () => api("/runs", z.array(runSchema));
export const getHealth = () => api("/health", healthSchema);

export const planSchema = z.object({
  id: z.string(),
  jobs: z.number(),
  episodes: z.number(),
  estimated_cost: z.string().nullable(),
  reserved_cost: z.string(),
  budget: z.string(),
  episode_caps_total: z.string(),
  blockers: z.array(z.string()),
  executable: z.boolean(),
  manifest: z.object({
    benchmarks: z.array(z.string()),
    comparison: z.string(),
    variants: z.array(
      z.object({
        id: z.string(),
        harness: z.string(),
        effort: z.string(),
        effort_status: z.string(),
        effective_effort: z.string().nullable(),
        connection: z.object({ model: z.string() }),
      }),
    ),
  }),
});
export type Plan = z.infer<typeof planSchema>;
