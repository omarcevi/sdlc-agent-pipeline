import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

afterEach(cleanup);

function Boom(): never {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  it("shows the message and a link back when a child throws", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText("Could not show this replay")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to all replays" }).getAttribute("href")).toBe("#/");
    vi.restoreAllMocks();
  });

  it("renders its children when nothing throws", () => {
    render(<ErrorBoundary>fine</ErrorBoundary>);
    expect(screen.getByText("fine")).toBeTruthy();
  });
});
