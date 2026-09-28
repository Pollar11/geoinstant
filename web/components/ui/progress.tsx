import { cn } from "@/lib/utils";

export function Progress({ value, className, label }: { value: number; className?: string; label: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v)}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
    >
      <div className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out" style={{ width: `${v}%` }} />
    </div>
  );
}
