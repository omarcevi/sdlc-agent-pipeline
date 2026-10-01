import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { HowToRead } from "./HowToRead";

afterEach(cleanup);

describe("HowToRead", () => {
  it("lists the four points of the design", () => {
    render(<HowToRead />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText(/hidden tests, run after the run in fresh sandboxes/)).toBeTruthy();
  });
});
