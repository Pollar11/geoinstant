"use client";

import { Camera, ImageUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ACCEPT } from "@/lib/prepare-image";
import { cn } from "@/lib/utils";

/** Drag-and-drop, file picker, camera capture (mobile) and paste (Ctrl/⌘+V). */
export function Dropzone({ onFile, compact = false }: { onFile: (f: File) => void; compact?: boolean }) {
  const [over, setOver] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);

  const take = useCallback(
    (files: FileList | null | undefined) => {
      const f = files?.[0];
      if (f) onFile(f);
    },
    [onFile],
  );

  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const f = Array.from(e.clipboardData?.files ?? []).find((x) => x.type.startsWith("image/"));
      if (f) onFile(f);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [onFile]);

  const inputs = (
    <>
      <input ref={pick} type="file" accept={ACCEPT} className="sr-only" onChange={(e) => take(e.target.files)} />
      <input ref={camera} type="file" accept="image/*" capture="environment" className="sr-only" onChange={(e) => take(e.target.files)} />
    </>
  );

  if (compact) {
    return (
      <div className="flex gap-2">
        {inputs}
        <Button variant="outline" size="sm" aria-label="New photo" onClick={() => pick.current?.click()}>
          <ImageUp /> <span className="hidden sm:inline">New photo</span>
        </Button>
        <Button variant="outline" size="sm" aria-label="Take photo" className="sm:hidden" onClick={() => camera.current?.click()}>
          <Camera />
        </Button>
      </div>
    );
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
      className={cn(
        "flex min-h-72 flex-col items-center justify-center gap-4 rounded-lg border-2 border-dashed p-8 text-center transition-colors",
        over ? "border-primary bg-primary/5" : "bg-card",
      )}
    >
      {inputs}
      <div className="rounded-full bg-primary/10 p-4 text-primary">
        <ImageUp className="size-8" aria-hidden />
      </div>
      <div className="space-y-1">
        <p className="text-lg font-semibold">Drop a photo to find where it was taken</p>
        <p className="text-sm text-muted-foreground">JPEG, PNG, WebP or HEIC · up to 25 MB · or paste with Ctrl/⌘ + V</p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        <Button size="lg" onClick={() => pick.current?.click()}>
          <ImageUp /> Choose photo
        </Button>
        <Button size="lg" variant="outline" className="sm:hidden" onClick={() => camera.current?.click()}>
          <Camera /> Take photo
        </Button>
      </div>
      <p className="max-w-md text-xs text-muted-foreground">
        Photos with GPS are read on your device and never uploaded. Others are shrunk, stripped of metadata and processed in
        memory - nothing is stored unless you choose to share it.
      </p>
    </div>
  );
}
