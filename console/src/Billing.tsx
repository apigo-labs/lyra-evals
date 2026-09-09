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
  const [open, setOpen] = useState(false),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <section className="rounded-xl border bg-white p-4 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span>
          实际账单：
          {status.data?.configured
            ? "已配置，每分钟自动同步"
            : "未配置平台访问权限"}
        </span>
        <Button variant="outline" onClick={() => setOpen(!open)}>
          配置账单连接
        </Button>
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
      {open && (
        <form
          className="mt-4 flex flex-wrap gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const fields = new FormData(form);
            setBusy(true);
            try {
              await api(
                "/billing/config",
                z.object({ configured: z.boolean() }),
                {
                  method: "POST",
                  body: JSON.stringify({
                    workspace_id: fields.get("workspace"),
                    token: fields.get("token"),
                  }),
                },
              );
              form.reset();
              setOpen(false);
              setMessage("已保存至本地私密目录");
              void client.invalidateQueries({ queryKey: ["billing"] });
            } catch (e) {
              setMessage(e instanceof Error ? e.message : "保存失败");
            } finally {
              setBusy(false);
            }
          }}
        >
          <input
            className="rounded border p-2"
            aria-label="Workspace ID"
            name="workspace"
            placeholder="Workspace ID"
            required
          />
          <input
            className="rounded border p-2"
            aria-label="平台访问令牌"
            name="token"
            type="password"
            autoComplete="off"
            placeholder="平台访问令牌（不是模型 API Key）"
            required
          />
          <Button disabled={busy}>保存连接</Button>
          <p className="w-full text-neutral-500">
            只访问 APIGO
            平台账单接口。令牌仅在本地服务端保存，不回显、不进入导出；到期后需更新。
          </p>
        </form>
      )}
      {(message || status.data?.error) && (
        <p role="status" className="mt-3">
          {message || status.data?.error}
        </p>
      )}
    </section>
  );
}
