import { describe, expect, it } from "vitest";
import * as drawing from "../shared/drawing";
const fn = (drawing as any).strokesBounds;
const half = drawing.DEFAULT_STROKE_WIDTH / 2;
describe("T1 strokesBounds", () => {
  it("R1: strokesBounds is exported", () => { expect(typeof fn).toBe("function"); });
  it("R2: margin is half the explicit width on every side", () => {
    expect(fn([{ points: [{ x: 0, y: 0 }, { x: 10, y: 20 }], width: 2 }])).toEqual({ minX: -1, minY: -1, maxX: 11, maxY: 21 });
  });
  it("R3: a stroke without width uses the default width", () => {
    expect(fn([{ points: [{ x: 5, y: 5 }] }])).toEqual({ minX: 5 - half, minY: 5 - half, maxX: 5 + half, maxY: 5 + half });
  });
  it("R4: spans every stroke", () => {
    expect(fn([{ points: [{ x: 0, y: 0 }], width: 2 }, { points: [{ x: 100, y: 50 }], width: 2 }])).toEqual({ minX: -1, minY: -1, maxX: 101, maxY: 51 });
  });
  it("R5: null when there are no points", () => {
    expect(fn([])).toBeNull();
    expect(fn([{ points: [] }])).toBeNull();
  });
  it("R6: non-finite points are ignored entirely, whichever coordinate is bad", () => {
    expect(fn([{ points: [{ x: 0, y: 0 }, { x: Number.NaN, y: 100 }, { x: Infinity, y: 5 }, { x: 50, y: Number.NaN }, { x: -20, y: Infinity }, { x: -Infinity, y: 3 }, { x: 10, y: 10 }], width: 2 }])).toEqual({ minX: -1, minY: -1, maxX: 11, maxY: 11 });
    expect(fn([{ points: [{ x: Number.NaN, y: Number.NaN }] }])).toBeNull();
  });
});
