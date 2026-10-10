import { describe, expect, it } from 'vitest';
import { starPath, sparkPath } from './mascotPaths';

describe('mascot paths and helpers', () => {
  it('generates a valid 10-point closed star path', () => {
    const path = starPath(24, 25.5, 19, 10);
    expect(path.startsWith('M')).toBe(true);
    expect(path.endsWith('Z')).toBe(true);
    // 10 points: 1 M command followed by 9 L commands
    const points = path.match(/[ML]/g);
    expect(points).toHaveLength(10);
  });

  it('generates a valid 4-point sparkle path', () => {
    const spark = sparkPath(20, 20, 3);
    expect(spark.startsWith('M')).toBe(true);
    expect(spark.endsWith('Z')).toBe(true);
    expect(spark.includes('Q')).toBe(true);
  });
});
