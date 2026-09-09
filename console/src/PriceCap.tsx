import { useState } from "react";
import { z } from "zod";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
export function PriceCap({ targetId }: { targetId: string }) {
  const [open, setOpen] = useState(false),
    [message, setMessage] = useState("");
  return (
    <div className="mt-2 text-xs">
      <button
        type="button"
        className="underline"
        onClick={() => setOpen(!open)}
      >
        配置内测价格上限
      </button>
      {open && (
        <form
          className="mt-2 flex flex-wrap gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            try {
              await api(
                `/targets/${targetId}/price-cap`,
                z.object({ id: z.string() }),
                {
                  method: "POST",
                  body: JSON.stringify({
                    input_usd_per_1m: Number(f.get("input")),
                    output_usd_per_1m: Number(f.get("output")),
                  }),
                },
              );
              setMessage("已保存，请重新冻结计划");
              setOpen(false);
            } catch (err) {
              setMessage(err instanceof Error ? err.message : "保存失败");
            }
          }}
        >
          <input
            name="input"
            type="number"
            min="0.000001"
            step="any"
            required
            aria-label="输入价格上限 USD 每百万 token"
            placeholder="输入 USD / 1M"
            className="rounded border p-2"
          />
          <input
            name="output"
            type="number"
            min="0.000001"
            step="any"
            required
            aria-label="输出价格上限 USD 每百万 token"
            placeholder="输出 USD / 1M"
            className="rounded border p-2"
          />
          <Button type="submit" variant="outline">
            保存上限
          </Button>
          <p className="w-full text-muted-foreground">
            仅在公开报价缺失时使用。填写供应商确认的计费上限，覆盖缓存和推理费用；用于预算预留，不等于实际账单。
          </p>
        </form>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
