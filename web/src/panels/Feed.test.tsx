import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Feed } from "./Feed";
import type { FeedItem, ToolResultStep } from "../replay/types";

afterEach(cleanup);

const result = (over: Partial<ToolResultStep> = {}): ToolResultStep => ({
  i: 5,
  t: 12,
  node: "coder",
  visit: 1,
  cost_usd: 0,
  tool_calls: 1,
  kind: "tool_result",
  call: "c1",
  tool: "read_file",
  error: null,
  result: { content: "hello" },
  ...over,
});

const items = (): FeedItem[] => [
  { kind: "separator", step: 1, t: 0, node: "coder", visit: 2, via: "retry" },
  {
    kind: "call",
    step: 3,
    t: 5,
    agent: "coder",
    call: { id: "c1", tool: "read_file", label: "read_file src/a.py", args: { path: "src/a.py" } },
  },
  { kind: "result", step: 5, t: 12, result: result() },
  { kind: "wait", step: 6, t: 40, seconds: 27.4 },
];

const noop = () => {};

describe("Feed", () => {
  it("one line per call and result under node separators", () => {
    render(<Feed items={items()} follow onFollowChange={noop} onSeek={noop} />);
    expect(screen.getByText("coder · visit 2 · via retry")).toBeTruthy();
    expect(screen.getByText("read_file src/a.py")).toBeTruthy();
    expect(screen.getAllByText(/read_file/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("27 s without events")).toBeTruthy();
  });

  it("separator without via omits the route", () => {
    const it: FeedItem[] = [{ kind: "separator", step: 1, t: 0, node: "planner", visit: 1, via: null }];
    render(<Feed items={it} follow onFollowChange={noop} onSeek={noop} />);
    expect(screen.getByText("planner · visit 1")).toBeTruthy();
  });

  it("set_model_response reads answer", () => {
    const it: FeedItem[] = [
      {
        kind: "call",
        step: 3,
        t: 5,
        agent: "planner",
        call: { id: "c9", tool: "set_model_response", label: "set_model_response", args: {} },
      },
    ];
    render(<Feed items={it} follow onFollowChange={noop} onSeek={noop} />);
    expect(screen.getByText("answer")).toBeTruthy();
    expect(screen.queryByText("set_model_response")).toBeNull();
  });

  it("marks errors", () => {
    const it: FeedItem[] = [{ kind: "result", step: 5, t: 12, result: result({ error: "file not found" }) }];
    render(<Feed items={it} follow onFollowChange={noop} onSeek={noop} />);
    expect(screen.getByText(/✗.*file not found/)).toBeTruthy();
  });

  it("expanding shows arguments and result with cut markers", () => {
    const cut = "head\n[... 5,000 characters cut ...]\ntail";
    const it = items();
    it[2] = { kind: "result", step: 5, t: 12, result: result({ result: { content: cut } }) };
    render(<Feed items={it} follow onFollowChange={noop} onSeek={noop} />);
    expect(screen.queryByText("src/a.py", { selector: "pre" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show details of read_file src/a.py" }));
    expect(screen.getByText("src/a.py", { selector: "pre" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show result of read_file" }));
    const pre = screen.getByText(/5,000 characters cut/);
    expect(pre.textContent).toBe(cut);
  });

  it("stops following when scrolled away and resumes on follow", () => {
    const onFollowChange = vi.fn();
    const { container, rerender } = render(
      <Feed items={items()} follow onFollowChange={onFollowChange} onSeek={noop} />,
    );
    const box = container.querySelector("[data-testid='feed-scroll']") as HTMLElement;
    Object.defineProperty(box, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(box, "clientHeight", { configurable: true, value: 200 });
    box.scrollTop = 800;
    fireEvent.scroll(box);
    box.scrollTop = 100; // the reader scrolls up
    fireEvent.scroll(box);
    expect(onFollowChange).toHaveBeenCalledWith(false);
    expect(screen.queryByRole("button", { name: "follow" })).toBeNull();
    rerender(<Feed items={items()} follow={false} onFollowChange={onFollowChange} onSeek={noop} />);
    fireEvent.click(screen.getByRole("button", { name: "follow" }));
    expect(onFollowChange).toHaveBeenLastCalledWith(true);
  });

  it("the app's own scroll in progress does not stop following", () => {
    const onFollowChange = vi.fn();
    const { container } = render(<Feed items={items()} follow onFollowChange={onFollowChange} onSeek={noop} />);
    const box = container.querySelector("[data-testid='feed-scroll']") as HTMLElement;
    Object.defineProperty(box, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(box, "clientHeight", { configurable: true, value: 200 });
    for (const top of [10, 120, 300, 560, 800]) {
      box.scrollTop = top; // mid-animation: far from the bottom, moving down
      fireEvent.scroll(box);
    }
    expect(onFollowChange).not.toHaveBeenCalled();
  });

  it("a set_model_response result reads answer", () => {
    const it: FeedItem[] = [
      { kind: "result", step: 5, t: 12, result: result({ tool: "set_model_response", result: { ok: "yes" } }) },
    ];
    render(<Feed items={it} follow onFollowChange={noop} onSeek={noop} />);
    expect(screen.queryByText("set_model_response")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show result of answer" }));
    expect(screen.getByText("yes")).toBeTruthy();
  });

  it("clicking a line seeks to its time", () => {
    const onSeek = vi.fn();
    const onFollowChange = vi.fn();
    render(<Feed items={items()} follow onFollowChange={onFollowChange} onSeek={onSeek} />);
    fireEvent.click(screen.getByRole("button", { name: "Seek to 00:12 (read_file)" }));
    expect(onSeek).toHaveBeenCalledWith(12);
    const line = screen.getByText("27 s without events").closest("li") as HTMLElement;
    fireEvent.click(within(line).getByRole("button", { name: /Seek to 00:40/ }));
    expect(onSeek).toHaveBeenCalledWith(40);
  });
});
