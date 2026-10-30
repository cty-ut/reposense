// Pure logic for the import graph: lookups per file, aggregation to the map's current level, and a
// layered layout (importers on the left, what they import on the right). No React here.
import { DependencyEdge, MapNode } from '../../shared/protocol';

export interface Related {
  path: string;
  weight: number;
}

const TEST_PATH = [/(^|\/)(tests?|__tests__|spec|testing)\//, /(^|\/)test_[^/]+\.py$/, /_test\.(py|go)$/, /\.(test|spec)\.[cm]?[jt]sx?$/];

export function isTestFile(path: string): boolean {
  return TEST_PATH.some((re) => re.test(path));
}

export class DependencyIndex {
  private readonly out = new Map<string, Map<string, number>>();
  private readonly in = new Map<string, Map<string, number>>();

  constructor(edges: DependencyEdge[]) {
    for (const e of edges) {
      add(this.out, e.source, e.target, e.weight);
      add(this.in, e.target, e.source, e.weight);
    }
  }

  /** Files this file imports. */
  uses(path: string): Related[] {
    return sorted(this.out.get(path));
  }

  /** Non-test files that import this file. */
  usedBy(path: string): Related[] {
    return sorted(this.in.get(path)).filter((r) => !isTestFile(r.path));
  }

  /** Test files that import this file: the tests to run after changing it. */
  testedBy(path: string): Related[] {
    return sorted(this.in.get(path)).filter((r) => isTestFile(r.path));
  }

  outgoing(path: string): Map<string, number> | undefined {
    return this.out.get(path);
  }
}

function add(index: Map<string, Map<string, number>>, from: string, to: string, weight: number): void {
  const row = index.get(from) ?? new Map<string, number>();
  row.set(to, (row.get(to) ?? 0) + weight);
  index.set(from, row);
}

function sorted(row: Map<string, number> | undefined): Related[] {
  return [...(row ?? [])].map(([path, weight]) => ({ path, weight })).sort((a, b) => b.weight - a.weight);
}

// ---------------------------------------------------------------- aggregation

export interface GraphEdge {
  source: string; // MapNode id
  target: string;
  weight: number;
}

function filesUnder(node: MapNode, out: string[] = []): string[] {
  if (node.kind === 'file') out.push(node.path);
  node.children?.forEach((c) => filesUnder(c, out));
  return out;
}

/** Edges between the given sibling nodes: file imports rolled up to whichever node contains each file. */
export function aggregate(nodes: MapNode[], index: DependencyIndex): GraphEdge[] {
  const owner = new Map<string, string>();
  for (const n of nodes) for (const f of filesUnder(n)) owner.set(f, n.id);
  const weights = new Map<string, number>();
  for (const [file, from] of owner) {
    for (const [target, w] of index.outgoing(file) ?? []) {
      const to = owner.get(target);
      if (!to || to === from) continue;
      const key = `${from}\n${to}`;
      weights.set(key, (weights.get(key) ?? 0) + w);
    }
  }
  return [...weights].map(([key, weight]) => {
    const [source, target] = key.split('\n');
    return { source, target, weight };
  });
}

// ---------------------------------------------------------------- layered layout

export interface Placed {
  node: MapNode;
  x: number;
  y: number;
  rank: number;
}

/** LR: importers on the left, imports on the right. TB: importers on top. */
export type Direction = 'LR' | 'TB';

export interface Layout {
  direction: Direction;
  placed: Placed[];
  isolated: MapNode[];   // nodes with no import relation at this level
  /** back: closes a cycle. implied: also reachable through other modules (A→B→C makes A→C implied). */
  edges: (GraphEdge & { back: boolean; implied: boolean })[];
  width: number;
  height: number;
}

export const NODE_W = 180;
export const NODE_H = 58;
const RANK_GAP = { LR: 72, TB: 46 };  // between ranks, along the direction of the arrows
const SIBLING_GAP = { LR: 14, TB: 16 };  // between nodes of the same rank

/** Edges that a longer path already explains; hiding them keeps the overview readable. */
function impliedEdges(forward: GraphEdge[]): Set<string> {
  const succ = new Map<string, string[]>();
  forward.forEach((e) => succ.set(e.source, [...(succ.get(e.source) ?? []), e.target]));
  const reachableAvoiding = (from: string, to: string): boolean => {
    const seen = new Set<string>();
    const stack = (succ.get(from) ?? []).filter((n) => n !== to);
    while (stack.length) {
      const n = stack.pop()!;
      if (n === to) return true;
      if (seen.has(n)) continue;
      seen.add(n);
      stack.push(...(succ.get(n) ?? []));
    }
    return false;
  };
  return new Set(forward.filter((e) => reachableAvoiding(e.source, e.target)).map((e) => `${e.source}\n${e.target}`));
}

/**
 * Sugiyama-style layout in three steps: break cycles (edges that close a cycle are drawn as "back"
 * edges), rank by longest path so every import points forward, then order each rank by the
 * average position of its neighbours to reduce crossings.
 */
export function layered(nodes: MapNode[], edges: GraphEdge[], direction: Direction): Layout {
  const connected = new Set(edges.flatMap((e) => [e.source, e.target]));
  const active = nodes.filter((n) => connected.has(n.id)).sort((a, b) => b.lines - a.lines);
  const isolated = nodes.filter((n) => !connected.has(n.id));

  // 1. cycle breaking with a DFS (visiting big modules first keeps the main flow forward)
  const succ = new Map<string, string[]>(active.map((n) => [n.id, []]));
  edges.forEach((e) => succ.get(e.source)!.push(e.target));
  const state = new Map<string, 'open' | 'done'>();
  const back = new Set<string>();
  const visit = (id: string) => {
    state.set(id, 'open');
    for (const t of succ.get(id)!) {
      if (state.get(t) === 'open') back.add(`${id}\n${t}`);
      else if (!state.has(t)) visit(t);
    }
    state.set(id, 'done');
  };
  active.forEach((n) => state.has(n.id) || visit(n.id));
  const forward = edges.filter((e) => !back.has(`${e.source}\n${e.target}`));

  // 2. longest-path ranking
  const rank = new Map<string, number>(active.map((n) => [n.id, 0]));
  for (let changed = true, guard = 0; changed && guard < active.length + 1; guard++) {
    changed = false;
    for (const e of forward) {
      if (rank.get(e.target)! < rank.get(e.source)! + 1) {
        rank.set(e.target, rank.get(e.source)! + 1);
        changed = true;
      }
    }
  }

  // 3. order columns by barycenter, sweeping left→right and right→left a few times
  const columns: MapNode[][] = [];
  active.forEach((n) => (columns[rank.get(n.id)!] ??= []).push(n));
  const pos = new Map<string, number>();
  const index = () => columns.forEach((col) => col.forEach((n, i) => pos.set(n.id, i)));
  index();
  const neighbours = (id: string, dir: 'in' | 'out') =>
    forward.filter((e) => (dir === 'in' ? e.target === id : e.source === id)).map((e) => (dir === 'in' ? e.source : e.target));
  for (let sweep = 0; sweep < 4; sweep++) {
    const dir = sweep % 2 === 0 ? 'in' : 'out';
    for (const col of dir === 'in' ? columns : [...columns].reverse()) {
      const bary = new Map(col.map((n) => {
        const ns = neighbours(n.id, dir);
        return [n.id, ns.length ? ns.reduce((s, id) => s + pos.get(id)!, 0) / ns.length : pos.get(n.id)!];
      }));
      col.sort((a, b) => bary.get(a.id)! - bary.get(b.id)!);
      col.forEach((n, i) => pos.set(n.id, i));
    }
  }

  // 4. coordinates: ranks advance along the arrows, siblings spread across them, each rank centered
  const along = (direction === 'LR' ? NODE_W : NODE_H) + RANK_GAP[direction];
  const across = (direction === 'LR' ? NODE_H : NODE_W) + SIBLING_GAP[direction];
  const widest = Math.max(1, ...columns.map((c) => c.length));
  const span = widest * across - SIBLING_GAP[direction];
  const placed: Placed[] = [];
  columns.forEach((col, r) => {
    const offset = (span - (col.length * across - SIBLING_GAP[direction])) / 2;
    col.forEach((node, i) => {
      const a = r * along, b = offset + i * across;
      placed.push({ node, rank: r, x: direction === 'LR' ? a : b, y: direction === 'LR' ? b : a });
    });
  });
  const depth = columns.length * along - RANK_GAP[direction];
  const implied = impliedEdges(forward);
  return {
    direction,
    placed,
    isolated,
    edges: edges.map((e) => {
      const key = `${e.source}
${e.target}`;
      return { ...e, back: back.has(key), implied: implied.has(key) };
    }),
    width: placed.length ? (direction === 'LR' ? depth : span) : 0,
    height: placed.length ? (direction === 'LR' ? span : depth) : 0,
  };
}
