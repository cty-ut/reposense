// Unit tests for the import-graph logic (pure functions, no DOM).
import { describe, expect, it } from 'vitest';
import { DependencyEdge, MapNode } from '../../shared/protocol';
import { aggregate, DependencyIndex, layered } from './graphLayout';
import { collapse } from './tree';

const file = (path: string, lines = 10): MapNode => ({
  id: path, name: path.split('/').pop()!, path, kind: 'file', layer: null, lines, files: 1, churn: 0,
});
const dir = (path: string, children: MapNode[]): MapNode => ({
  id: path, name: path.split('/').pop()!, path, kind: 'dir', layer: null, children,
  lines: children.reduce((s, c) => s + c.lines, 0), files: children.length, churn: 0,
});
const edge = (source: string, target: string, weight = 1): DependencyEdge => ({ source, target, weight });

describe('DependencyIndex', () => {
  const index = new DependencyIndex(
    [edge('app.py', 'core.py', 3), edge('app.py', 'util.py', 7), edge('cli.py', 'core.py'), edge('tests/test_core.py', 'core.py')],
    new Set(['tests/test_core.py']),
  );

  it('lists imports by weight', () => {
    expect(index.uses('app.py').map((r) => r.path)).toEqual(['util.py', 'core.py']);
  });

  it('keeps tests apart from the code that depends on a file', () => {
    expect(index.usedBy('core.py').map((r) => r.path).sort()).toEqual(['app.py', 'cli.py']);
    expect(index.testedBy('core.py').map((r) => r.path)).toEqual(['tests/test_core.py']);
  });
});

describe('aggregate', () => {
  it('rolls file imports up to the sibling nodes that contain them', () => {
    const web = dir('web', [file('web/a.ts'), file('web/b.ts')]);
    const api = dir('api', [file('api/x.py')]);
    const index = new DependencyIndex([
      edge('web/a.ts', 'api/x.py', 2), edge('web/b.ts', 'api/x.py', 3),
      edge('web/a.ts', 'web/b.ts'),           // inside one node: not an edge at this level
      edge('web/a.ts', 'elsewhere/z.ts'),     // outside the shown nodes: dropped
    ]);
    expect(aggregate([web, api], index)).toEqual([{ source: 'web', target: 'api', weight: 5 }]);
  });
});

describe('layered', () => {
  const nodes = ['a', 'b', 'c', 'd', 'lonely'].map((p) => file(p));

  it('points every forward edge downstream and marks the edge that closes a cycle', () => {
    const layout = layered(nodes, [edge('a', 'b'), edge('b', 'c'), edge('c', 'a'), edge('c', 'd')], 'LR');
    const rank = new Map(layout.placed.map((p) => [p.node.id, p.rank]));
    expect(layout.edges.filter((e) => e.back)).toHaveLength(1);
    for (const e of layout.edges.filter((e) => !e.back)) expect(rank.get(e.target)!).toBeGreaterThan(rank.get(e.source)!);
    expect(layout.isolated.map((n) => n.id)).toEqual(['lonely']);
  });

  it('marks shortcuts that a longer path already explains', () => {
    const layout = layered(nodes, [edge('a', 'b'), edge('b', 'c'), edge('a', 'c')], 'LR');
    expect(layout.edges.filter((e) => e.implied).map((e) => `${e.source}>${e.target}`)).toEqual(['a>c']);
  });

  it('lays ranks out along the chosen direction', () => {
    const chain = [edge('a', 'b'), edge('b', 'c'), edge('c', 'd')];
    const lr = layered(nodes, chain, 'LR');
    const tb = layered(nodes, chain, 'TB');
    expect(lr.width).toBeGreaterThan(lr.height);
    expect(tb.height).toBeGreaterThan(tb.width);
  });
});

describe('collapse', () => {
  it('folds a chain of single directories into its first node', () => {
    const leaf = dir('src/pkg', [file('src/pkg/a.py'), file('src/pkg/b.py')]);
    const layer: MapNode = { ...dir('', [dir('src', [leaf])]), id: 'core', kind: 'layer', name: '核心库' };
    const folded = collapse(layer);
    expect(folded.name).toBe('核心库');
    expect(folded.path).toBe('src/pkg');
    expect(folded.children?.map((c) => c.id)).toEqual(['src/pkg/a.py', 'src/pkg/b.py']);
  });
});
