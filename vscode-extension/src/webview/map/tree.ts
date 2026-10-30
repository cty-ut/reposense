// Helpers over the map tree (MapNode) shared by the treemap, the graph and the page.
import { MapNode } from '../../shared/protocol';

/** A node whose only child is a directory (layer "测试" → tests/) shows that directory's contents directly. */
export function collapse(n: MapNode): MapNode {
  let inner = n;
  while (inner.children?.length === 1 && inner.children[0].kind === 'dir') inner = inner.children[0];
  return inner === n ? n : { ...n, path: inner.path, children: inner.children };
}

/** Copy of the tree with the one-line file descriptions filled in (the model's layer/dir texts win). */
export function withSummaries(node: MapNode, summaries: Record<string, string>): MapNode {
  const description = node.description || summaries[node.path] || null;
  const children = node.children?.map((c) => withSummaries(c, summaries));
  return { ...node, description, children };
}

export function indexById(root: MapNode): Map<string, MapNode> {
  const byId = new Map<string, MapNode>();
  const walk = (n: MapNode) => { byId.set(n.id, n); n.children?.forEach(walk); };
  walk(root);
  return byId;
}
