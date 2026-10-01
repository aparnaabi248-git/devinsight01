/** Parse the free-text changed-files textarea into the defect-risk request shape. */

export interface ParsedChange {
  path: string;
  additions: number;
  deletions: number;
}

export function parseChanges(raw: string): ParsedChange[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [path, add, del] = line.split(',').map((p) => p.trim());
      const toNumber = (value: string | undefined) =>
        Number((value ?? '0').replace(/^[+-]/, '')) || 0;
      return {
        path: path || 'unknown',
        additions: toNumber(add),
        deletions: toNumber(del),
      };
    });
}
