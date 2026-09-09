import { create } from "zustand";
type View = "results" | "overview" | "runs" | "models" | "benchmarks" | "theme";
export const useConsole = create<{
  view: View;
  setView: (v: View) => void;
  selected: string | null;
  select: (id: string | null) => void;
}>((set) => ({
  view: "overview",
  setView: (view) => set({ view }),
  selected: null,
  select: (selected) => set({ selected }),
}));
