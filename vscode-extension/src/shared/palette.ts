// Colors shared by the editor decorations and the webviews, so "commit #2" is the same color everywhere.

/** One hue per recent commit (newest first). */
export const COMMIT_COLORS = [
  '#4f9cf9', '#f5a524', '#17c964', '#f31260', '#9353d3',
  '#06b7db', '#ff7a45', '#8bc34a', '#e879f9', '#a1887f',
];

export function commitColor(index: number): string {
  return COMMIT_COLORS[index % COMMIT_COLORS.length];
}

/** Architecture layer colors on the repository map. */
export const LAYER_COLORS: Record<string, string> = {
  frontend: '#4f9cf9',
  backend: '#17c964',
  core: '#9353d3',
  cli: '#06b7db',
  api: '#14b8a6',
  data: '#f5a524',
  tests: '#f31260',
  docs: '#a1887f',
  examples: '#ff7a45',
  infra: '#64748b',
  tooling: '#8bc34a',
  other: '#94a3b8',
};

export function layerColor(kind: string | undefined | null): string {
  return LAYER_COLORS[kind ?? 'other'] ?? LAYER_COLORS.other;
}

/** `#rrggbb` + alpha (0..1) → `rgba(...)`. */
export function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
