// Colors shared by the editor decorations and the webviews, so "commit #2" is the same color everywhere.

/** One hue per recent commit (newest first). */
export const COMMIT_COLORS = [
  '#4f9cf9', '#f5a524', '#17c964', '#f31260', '#9353d3',
  '#06b7db', '#ff7a45', '#8bc34a', '#e879f9', '#a1887f',
];

export function commitColor(index: number): string {
  return COMMIT_COLORS[index % COMMIT_COLORS.length];
}

/**
 * Architecture layer colors on the repository map. Code layers get clear hues; supporting layers
 * (tests, docs, infra…) get quiet ones so the code a newcomer will change stands out.
 */
export const LAYER_COLORS: Record<string, string> = {
  frontend: '#3b82f6',
  backend: '#0f9f6e',
  core: '#6d5bd0',
  cli: '#0891b2',
  api: '#0d9488',
  data: '#d97706',
  tests: '#5b7fa6',
  docs: '#a08660',
  examples: '#c07a50',
  infra: '#7a8494',
  tooling: '#7d9455',
  other: '#8f96a3',
};

export function layerColor(kind: string | undefined | null): string {
  return LAYER_COLORS[kind ?? 'other'] ?? LAYER_COLORS.other;
}

/** `#rrggbb` + alpha (0..1) → `rgba(...)`. */
export function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
