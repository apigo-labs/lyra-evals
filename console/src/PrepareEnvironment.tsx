import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
const schema = z.object({ status: z.string(), message: z.string() });
export function PrepareEnvironment({ planId }: { planId: string }) {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const state = useQuery({
    queryKey: ["environment", planId],
    queryFn: () => api(`/plans/${planId}/environment`, schema),
    refetchInterval: 2000,
  });
  return (
    <div className="rounded-lg border p-3">
      <Button
        type="button"
        variant="outline"
        disabled={state.data?.status === "preparing"}
        onClick={async () => {
          try {
            await api(`/plans/${planId}/environment`, schema, {
              method: "POST",
              body: "{}",
            });
            setError("");
            void client.invalidateQueries({
              queryKey: ["environment", planId],
            });
          } catch (e) {
            setError(e instanceof Error ? e.message : "环境准备失败");
          }
        }}
      >
        准备 SWE 实例环境
      </Button>
      <p role="status" className="mt-2">
        {error || state.data?.message}
      </p>
      <p className="text-muted-foreground">
        仅下载本计划选中的官方实例镜像，可能占用数 GB
        磁盘；不调用模型、不消耗模型预算。
      </p>
    </div>
  );
}
