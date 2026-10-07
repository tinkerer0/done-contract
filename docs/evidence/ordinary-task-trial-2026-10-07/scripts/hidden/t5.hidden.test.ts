import { describe, expect, it } from "vitest";
import * as stats from "../shared/strokeStats";
const length = (stats as any).strokeLength;
const ink = (stats as any).totalInk;
describe("T5 strokeStats", () => {
  it("R1: strokeLength and totalInk are exported", () => { expect(typeof length).toBe("function"); expect(typeof ink).toBe("function"); });
  it("R2: strokeLength sums the segment lengths", () => {
    expect(length({ points: [{ x: 0, y: 0 }, { x: 3, y: 4 }] })).toBe(5);
    expect(length({ points: [{ x: 0, y: 0 }, { x: 3, y: 4 }, { x: 3, y: 10 }] })).toBe(11);
  });
  it("R3: strokeLength is 0 below two points", () => {
    expect(length({ points: [] })).toBe(0);
    expect(length({ points: [{ x: 1, y: 1 }] })).toBe(0);
  });
  it("R4: totalInk is length times width, default width when missing", () => {
    expect(ink([{ points: [{ x: 0, y: 0 }, { x: 10, y: 0 }] }])).toBeCloseTo(27.5);
    expect(ink([{ points: [{ x: 0, y: 0 }, { x: 10, y: 0 }], width: 2 }])).toBeCloseTo(20);
  });
  it("R5: totalInk sums strokes and is 0 for none", () => {
    expect(ink([{ points: [{ x: 0, y: 0 }, { x: 10, y: 0 }], width: 2 }, { points: [{ x: 0, y: 0 }, { x: 0, y: 5 }], width: 4 }])).toBeCloseTo(40);
    expect(ink([])).toBe(0);
  });
});
