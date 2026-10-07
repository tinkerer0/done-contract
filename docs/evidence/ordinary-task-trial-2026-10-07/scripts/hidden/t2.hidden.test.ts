import { describe, expect, it } from "vitest";
import { convertLegacyStrokes } from "../shared/drawing";
describe("T2 legacy width", () => {
  it("R1: stroke width survives the conversion", () => {
    expect(convertLegacyStrokes([{ points: [{ x: 0.5, y: 0.5 }], width: 5 }], 100, 100)).toEqual([{ points: [{ x: 50, y: 50 }], width: 5 }]);
  });
  it("R2: strokes without a width stay without one", () => {
    const [stroke] = convertLegacyStrokes([{ points: [{ x: 0.5, y: 0.5 }] }], 100, 100);
    expect("width" in stroke).toBe(false);
  });
  it("R3: an invalid canvas size returns the input untouched", () => {
    const input = [{ points: [{ x: 0.5, y: 0.5 }], width: 5 }];
    expect(convertLegacyStrokes(input, 0, 100)).toBe(input);
  });
});
