import { z } from "zod";
import { getRuns, getHealth } from "./api";
type Registry = {
  registerTool: (
    tool: {
      name: string;
      description: string;
      inputSchema: object;
      annotations: object;
      execute: (input: unknown) => Promise<unknown>;
    },
    options: { signal: AbortSignal },
  ) => unknown;
};
export function registerReadTools() {
  const context = (document as Document & { modelContext?: Registry })
    .modelContext;
  if (!context?.registerTool) return () => {};
  const lifecycle = new AbortController();
  try {
    Promise.resolve(
      context.registerTool(
        {
          name: "get_lyra_execution_status",
          description:
            "读取本地 Lyra 的运行状态与环境门禁，不创建或启动任务，不读取 API Key。",
          inputSchema: {
            type: "object",
            properties: {},
            additionalProperties: false,
          },
          annotations: { readOnlyHint: true, untrustedContentHint: true },
          execute: async (input) => {
            z.object({}).strict().parse(input);
            const [runs, health] = await Promise.all([getRuns(), getHealth()]);
            return {
              health,
              runs: runs.map((r) => ({
                id: r.id,
                name: r.name,
                status: r.status,
                mode: r.mode,
                completed: r.jobs.reduce((n, j) => n + j.completed, 0),
              })),
            };
          },
        },
        { signal: lifecycle.signal },
      ),
    ).catch(() => {});
  } catch {
    /* Optional browser capability; normal UI remains available. */
  }
  return () => lifecycle.abort();
}
