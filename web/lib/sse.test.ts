import { describe, expect, it } from "vitest";

import { readSse } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
}

async function collect(chunks: string[]) {
  const out: string[] = [];
  for await (const d of readSse(streamOf(chunks))) out.push(d);
  return out;
}

describe("readSse", () => {
  it("splits events and strips the data prefix", async () => {
    expect(await collect(['event: stage\ndata: {"a":1}\n\n', 'data: {"b":2}\n\n'])).toEqual(['{"a":1}', '{"b":2}']);
  });

  it("reassembles events split across chunks (even mid-field and mid-UTF-8)", async () => {
    const payload = 'data: {"place":"Zürich"}\n\n';
    const bytes = new TextEncoder().encode(payload);
    const parts = [bytes.slice(0, 7), bytes.slice(7, 18), bytes.slice(18)];
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        parts.forEach((p) => c.enqueue(p));
        c.close();
      },
    });
    const out: string[] = [];
    for await (const d of readSse(stream)) out.push(d);
    expect(out).toEqual(['{"place":"Zürich"}']);
  });

  it("handles CRLF, multi-line data and a missing final blank line", async () => {
    expect(await collect(["data: line1\r\ndata: line2\r\n\r\n", "data: last"])).toEqual(["line1\nline2", "last"]);
  });
});
