import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
export function Billing() {
  const client = useQueryClient();
  const status = useQuery({
    queryKey: ["billing"],
    queryFn: () =>
      api(
        "/billing",
        z.object({ configured: z.boolean(), error: z.string().nullable() }),
      ),
    refetchInterval: 15000,
  });
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <section className="rounded-xl border bg-white p-4 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span>
          实际账单：
          {status.data?.configured
            ? "已配置，每分钟自动同步"
            : "未配置平台访问凭据"}
        </span>
        <Button
          variant="outline"
          disabled={!status.data?.configured || busy}
          onClick={async () => {
            setBusy(true);
            try {
              const r = await api(
                "/billing/reconcile",
                z.object({
                  settled_requests: z.number(),
                  pending_requests: z.number(),
                }),
                { method: "POST", body: "{}" },
              );
              setMessage(
                `本次结算 ${r.settled_requests} 次，待确认 ${r.pending_requests} 次`,
              );
              void client.invalidateQueries({ queryKey: ["results"] });
            } catch (e) {
              setMessage(e instanceof Error ? e.message : "同步失败");
            } finally {
              setBusy(false);
            }
          }}
        >
          同步账单
        </Button>
      </div>
      <p className="mt-3 text-neutral-500">
        平台访问凭据（VOHU_EVALS_PLATFORM_BASE_URL / VOHU_EVALS_WORKSPACE_ID /
        VOHU_EVALS_PLATFORM_TOKEN 或 VOHU_USER_EMAIL /
        VOHU_USER_PASSWORD）只在本地 .env 中配置，与 CLI 共用；令牌过期时服务端会自动
        登录刷新。此处仅显示状态，不接受输入。
      </p>
      {(message || status.data?.error) && (
        <p role="status" className="mt-3">
          {message || status.data?.error}
        </p>
      )}
    </section>
  );
}
