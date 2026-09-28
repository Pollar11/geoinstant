"use client";

import { CheckCircle2, Circle, CircleSlash, Clock, Loader2, XCircle } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { formatMs } from "@/lib/format";
import { cn } from "@/lib/utils";
import { STAGES, type Phase, type StageState } from "@/lib/use-locate";
import type { StageName } from "@/lib/api-types";

function Icon({ state, active }: { state: StageState["status"]; active: boolean }) {
  if (state === "done") return <CheckCircle2 className="size-4 text-success" aria-label="done" />;
  if (state === "skipped") return <CircleSlash className="size-4 text-muted-foreground" aria-label="skipped" />;
  if (state === "timeout") return <Clock className="size-4 text-warning" aria-label="timed out" />;
  if (state === "error") return <XCircle className="size-4 text-danger" aria-label="failed" />;
  if (state === "running" || active) return <Loader2 className="size-4 animate-spin text-primary" aria-label="running" />;
  return <Circle className="size-4 text-muted-foreground/50" aria-label="pending" />;
}

export function PipelineTimeline({ stages, phase, elapsed }: { stages: Record<StageName, StageState>; phase: Phase; elapsed: number }) {
  const settled = STAGES.filter((s) => stages[s.name].status !== "pending" && stages[s.name].status !== "running").length;
  const pct = phase === "done" ? 100 : (settled / STAGES.length) * 100;
  const firstPending = STAGES.find((s) => stages[s.name].status === "pending")?.name;

  return (
    <div className="space-y-3" aria-live="polite">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{phase === "done" ? "Located" : phase === "error" ? "Stopped" : "Locating…"}</span>
        <span className="font-mono tabular-nums text-muted-foreground">{formatMs(elapsed)}</span>
      </div>
      <Progress value={pct} label="Pipeline progress" />
      <ol className="space-y-1.5">
        {STAGES.map((s) => {
          const st = stages[s.name];
          const active = (phase === "running" || phase === "preparing") && s.name === firstPending;
          return (
            <li key={s.name} className={cn("flex items-center gap-2 text-sm", st.status === "pending" && !active && "text-muted-foreground")}>
              <Icon state={st.status} active={active} />
              <span className="flex-1 truncate">{s.label}</span>
              {st.detail && <span className="hidden truncate text-xs text-muted-foreground sm:inline">{st.detail}</span>}
              <span className="w-14 text-right font-mono text-xs tabular-nums text-muted-foreground">{formatMs(st.ms)}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
