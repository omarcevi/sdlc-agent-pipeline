import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Transport, type TransportProps } from "./Transport";

afterEach(cleanup);

const SPEEDS = [1, 2, 5, 10, 25, 50] as const;

function props(over: Partial<TransportProps> = {}): TransportProps {
  return {
    t: 65,
    end: 125,
    playing: false,
    speed: 10,
    speeds: SPEEDS,
    skipWaits: true,
    ticks: [
      { t: 10, node: "planner" },
      { t: 60, node: "coder" },
    ],
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onSeek: vi.fn(),
    onStep: vi.fn(),
    onStart: vi.fn(),
    onEnd: vi.fn(),
    onSpeed: vi.fn(),
    onSkipWaits: vi.fn(),
    ...over,
  };
}

describe("Transport", () => {
  it("every control has an accessible name", () => {
    render(<Transport {...props()} />);
    for (const name of ["Start", "Previous step", "Play", "Next step", "End"])
      expect(screen.getByRole("button", { name })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Speed" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "skip long waits" })).toBeTruthy();
    expect(screen.getByRole("slider", { name: "Scrubber" })).toBeTruthy();
    expect(screen.getByText("01:05 / 02:05")).toBeTruthy();
    expect(screen.getAllByTestId("tick")).toHaveLength(2);
    for (const b of screen.getAllByRole("button")) expect(b.getAttribute("aria-label")).toBeTruthy();
  });

  it("buttons call their handlers, and Play becomes Pause", () => {
    const p = props();
    const { rerender } = render(<Transport {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    fireEvent.click(screen.getByRole("button", { name: "End" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous step" }));
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(p.onPlay).toHaveBeenCalled();
    expect(p.onStart).toHaveBeenCalled();
    expect(p.onEnd).toHaveBeenCalled();
    expect(p.onStep).toHaveBeenNthCalledWith(1, -1);
    expect(p.onStep).toHaveBeenNthCalledWith(2, 1);
    rerender(<Transport {...p} playing />);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(p.onPause).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "skip long waits" }));
    expect(p.onSkipWaits).toHaveBeenCalledWith(false);
  });

  it("offers the six speeds", () => {
    const p = props();
    render(<Transport {...p} />);
    const opts = screen.getAllByRole("option").map((o) => o.textContent);
    expect(opts).toEqual(["1×", "2×", "5×", "10×", "25×", "50×"]);
    fireEvent.change(screen.getByRole("combobox", { name: "Speed" }), { target: { value: "25" } });
    expect(p.onSpeed).toHaveBeenCalledWith(25);
  });

  it("the scrubber seeks", () => {
    const p = props();
    render(<Transport {...p} />);
    fireEvent.change(screen.getByRole("slider", { name: "Scrubber" }), { target: { value: "90" } });
    expect(p.onSeek).toHaveBeenCalledWith(90);
  });
});
