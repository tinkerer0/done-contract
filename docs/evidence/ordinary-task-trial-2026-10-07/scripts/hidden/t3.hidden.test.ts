import { describe, expect, it } from "vitest";
import * as drawing from "../shared/drawing";
const fn = (drawing as any).simplifyStroke;
const P = (x: number, y: number) => ({ x, y });
describe("T3 simplifyStroke", () => {
  it("R1: simplifyStroke is exported", () => { expect(typeof fn).toBe("function"); });
  it("R2: collinear middle points are removed", () => {
    expect(fn({ points: [P(0, 0), P(5, 0), P(10, 0), P(20, 0)] }, 0.5).points).toEqual([P(0, 0), P(20, 0)]);
  });
  it("R3: prominent vertices and both endpoints are kept", () => {
    const pts = [P(0, 0), P(10, 10), P(20, 0), P(30, 10), P(40, 0)];
    expect(fn({ points: pts }, 1).points).toEqual(pts);
  });
  it("R4: wiggles below the tolerance are flattened", () => {
    expect(fn({ points: [P(0, 0), P(5, 0.1), P(10, -0.1), P(15, 0.1), P(20, 0)] }, 1).points).toEqual([P(0, 0), P(20, 0)]);
  });
  it("R5: the stroke width is preserved and never invented", () => {
    expect(fn({ points: [P(0, 0), P(5, 0), P(10, 0)], width: 5 }, 0.5).width).toBe(5);
    expect("width" in fn({ points: [P(0, 0), P(5, 0), P(10, 0)] }, 0.5)).toBe(false);
  });
  it("R6: two or fewer points come back as they were", () => {
    expect(fn({ points: [P(0, 0), P(1, 1)] }, 5)).toEqual({ points: [P(0, 0), P(1, 1)] });
    expect(fn({ points: [P(2, 2)] }, 5)).toEqual({ points: [P(2, 2)] });
    expect(fn({ points: [] }, 5)).toEqual({ points: [] });
  });
  it("R7: tolerance of zero or less returns a copy with the same points", () => {
    const stroke = { points: [P(0, 0), P(5, 0), P(10, 0)], width: 3 };
    for (const tol of [0, -1]) {
      const out = fn(stroke, tol);
      expect(out).toEqual(stroke);
      expect(out).not.toBe(stroke);
    }
  });
  it("R8: the input is not mutated", () => {
    const stroke = { points: [P(0, 0), P(5, 0), P(10, 0), P(20, 0)], width: 2 };
    const copy = JSON.parse(JSON.stringify(stroke));
    fn(stroke, 0.5);
    expect(stroke).toEqual(copy);
  });
  it("R9: tolerance of zero or less always returns a distinct copy, whatever the point count", () => {
    for (const pts of [[], [P(1, 1)], [P(0, 0), P(1, 1)]]) {
      for (const tol of [0, -1]) {
        const stroke = { points: pts };
        const out = fn(stroke, tol);
        expect(out).toEqual(stroke);
        expect(out).not.toBe(stroke);
      }
    }
  });
});
