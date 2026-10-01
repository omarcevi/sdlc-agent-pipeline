import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// F15: the needles are built from pieces so this file never contains them, and it skips itself anyway.
const NEEDLES = ["dangerously" + "SetInnerHTML", "." + "inner" + "HTML"];
const SELF = "safety.test.ts";
const SRC = join(process.cwd(), "src"); // vitest runs from web/

function* sources(dir: string): Generator<string> {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* sources(p);
    else if (/\.(ts|tsx|js|jsx|html)$/.test(e.name) && e.name !== SELF) yield p;
  }
}

describe("web/src safety", () => {
  it("no dangerouslySetInnerHTML anywhere in web/src", () => {
    const hits: string[] = [];
    for (const f of sources(SRC)) {
      const text = readFileSync(f, "utf8");
      for (const n of NEEDLES) if (text.includes(n)) hits.push(`${f}: ${n}`);
    }
    expect(hits).toEqual([]);
  });
});
