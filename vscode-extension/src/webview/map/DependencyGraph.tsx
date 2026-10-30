// Import graph view: the focused node's children as boxes, imports between them as arrows
// (importers first, what they import after: left→right or top→bottom, whichever fits). Hover a box
// to trace its relations.
import { useMemo, useState } from 'react';
import { layerColor, USED_BY_COLOR, USES_COLOR, withAlpha } from '../../shared/palette';
import { MapNode } from '../../shared/protocol';
import { compact } from '../format';
import { aggregate, Direction, DependencyIndex, layered, Layout, NODE_H, NODE_W, Placed } from './graphLayout';
import { collapse } from './tree';
import { useSize } from './useSize';

const PAD = 28;
const MIN_SCALE = 0.6;

interface Props {
  focus: MapNode;
  layerKinds: Record<string, string>;
  index: DependencyIndex | null;
  steps: Map<string, number> | null;  // file path -> step of the reading order
  onZoom: (node: MapNode) => void;
  onOpenFile: (path: string) => void;
  onHover: (node: MapNode | null, x?: number, y?: number) => void;
}

function edgePath(s: Placed, t: Placed, back: boolean, direction: Direction): string {
  if (direction === 'TB') {
    const sx = s.x + NODE_W / 2, tx = t.x + NODE_W / 2;
    if (back) {
      // closes a cycle: leave from the top of the importer, loop round into the bottom of the target
      const sy = s.y, ty = t.y + NODE_H;
      return `M ${sx} ${sy} C ${sx + 50} ${sy - 60}, ${tx + 50} ${ty + 60}, ${tx} ${ty}`;
    }
    const sy = s.y + NODE_H, ty = t.y;
    const dy = Math.max(24, (ty - sy) / 2);
    return `M ${sx} ${sy} C ${sx} ${sy + dy}, ${tx} ${ty - dy}, ${tx} ${ty}`;
  }
  const sy = s.y + NODE_H / 2, ty = t.y + NODE_H / 2;
  if (back) {
    const sx = s.x, tx = t.x + NODE_W;
    return `M ${sx} ${sy} C ${sx - 70} ${sy + 50}, ${tx + 70} ${ty + 50}, ${tx} ${ty}`;
  }
  const sx = s.x + NODE_W, tx = t.x;
  const dx = Math.max(40, (tx - sx) / 2);
  return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
}

const fitScale = (l: Layout, w: number, h: number) =>
  Math.min(1.1, (w - 2 * PAD) / Math.max(1, l.width), (h - 2 * PAD) / Math.max(1, l.height));

export function DependencyGraph({ focus, layerKinds, index, steps, onZoom, onOpenFile, onHover }: Props) {
  const { ref, w, h } = useSize();
  const [hot, setHot] = useState<string | null>(null);

  const nodes = useMemo(() => (collapse(focus).children ?? []).map(collapse), [focus]);
  const layout = useMemo(() => {
    if (!index) return null;
    const edges = aggregate(nodes, index);
    const [lr, tb] = [layered(nodes, edges, 'LR'), layered(nodes, edges, 'TB')];
    return fitScale(tb, w, h) > fitScale(lr, w, h) ? tb : lr;  // whichever shows the text larger
  }, [nodes, index, w, h]);

  if (!index || !layout) return <div className="graph-wrap"><div className="state">正在分析 import 关系…</div></div>;

  const at = new Map(layout.placed.map((p) => [p.node.id, p]));
  const maxWeight = Math.max(1, ...layout.edges.map((e) => e.weight));
  const degree = (id: string, side: 'source' | 'target') => layout.edges.filter((e) => e[side] === id).length;
  const neighbours = hot
    ? new Set(layout.edges.flatMap((e) => (e.source === hot ? [e.target] : e.target === hot ? [e.source] : [])))
    : null;
  const scale = Math.max(MIN_SCALE, fitScale(layout, w, h));  // below this the text is unreadable: scroll instead
  const colorOf = (n: MapNode) => layerColor(n.kind === 'layer' ? n.layer_kind : layerKinds[n.layer ?? ''] ?? 'other');
  const open = (n: MapNode) => (n.kind === 'file' ? onOpenFile(n.path) : onZoom(n));

  return (
    <div className="graph-wrap">
      <div className="graph-legend muted small">
        <span><i className="arrow" /> A → B：A import 了 B</span>
        <span><i className="arrow dashed" /> 循环依赖</span>
        <span>线越粗，引用越多</span>
        <span>只画主干：A→B→C 时省略 A→C</span>
        <span>悬停看关系，点击{nodes.some((n) => n.kind !== 'file') ? '放大' : '打开'}</span>
      </div>
      <div className="graph-canvas" ref={ref} onMouseLeave={() => { setHot(null); onHover(null); }}>
        {layout.placed.length === 0 ? (
          <div className="state">这一层的模块之间没有 import 关系。</div>
        ) : (
          <div className="graph-stage" style={{
            width: layout.width, height: layout.height, transform: `scale(${scale})`,
            left: Math.max(PAD, (w - layout.width * scale) / 2), top: Math.max(PAD, (h - layout.height * scale) / 2),
          }}>
            <svg className="graph-edges" width={layout.width} height={layout.height} overflow="visible">
              <defs>
                {[['plain', 'currentColor'], ['uses', USES_COLOR], ['used-by', USED_BY_COLOR]].map(([id, color]) => (
                  <marker key={id} id={`arrow-${id}`} viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z" fill={color} />
                  </marker>
                ))}
              </defs>
              {layout.edges.map((e) => {
                const role = !hot ? 'plain' : e.source === hot ? 'uses' : e.target === hot ? 'used-by' : 'faded';
                if (e.implied && (role === 'plain' || role === 'faded')) return null;  // shown only for the hovered node
                const color = role === 'uses' ? USES_COLOR : role === 'used-by' ? USED_BY_COLOR : 'currentColor';
                return (
                  <path key={`${e.source}>${e.target}`} d={edgePath(at.get(e.source)!, at.get(e.target)!, e.back, layout.direction)}
                        className={`edge ${role}`} stroke={color} strokeDasharray={e.back ? '5 4' : undefined}
                        strokeWidth={1.2 + 3.5 * (Math.log1p(e.weight) / Math.log1p(maxWeight))}
                        markerEnd={role === 'faded' ? undefined : `url(#arrow-${role})`}>
                    <title>{`${e.source.split('/').pop()} → ${e.target.split('/').pop()}：${e.weight} 处引用`}</title>
                  </path>
                );
              })}
            </svg>
            {layout.placed.map(({ node, x, y }) => {
              const dim = neighbours && hot !== node.id && !neighbours.has(node.id);
              const step = node.kind === 'file' ? steps?.get(node.path) : undefined;
              return (
                <div key={node.id} className={`gnode${dim ? ' dim' : ''}${hot === node.id ? ' hot' : ''}${step ? ' on-route' : ''}`}
                     style={{ left: x, top: y, width: NODE_W, height: NODE_H, borderLeftColor: colorOf(node) }}
                     onMouseMove={(e) => { setHot(node.id); onHover(node, e.clientX, e.clientY); }}
                     onClick={() => open(node)}>
                  {step && <span className="step-badge">{step}</span>}
                  <div className="gname ellipsis">{node.kind === 'dir' ? `${node.name}/` : node.name}</div>
                  <div className="muted ellipsis small">{node.description || node.path}</div>
                  <div className="muted ellipsis small">
                    {compact(node.lines)} 行 · 依赖 {degree(node.id, 'source')} · 被依赖 {degree(node.id, 'target')}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {layout.isolated.length > 0 && (
        <div className="graph-isolated">
          <span className="muted small">没有代码依赖：</span>
          {layout.isolated.map((n) => (
            <button key={n.id} className="chip" style={{ borderColor: withAlpha(colorOf(n), 0.5) }} onClick={() => open(n)}
                    onMouseMove={(e) => onHover(n, e.clientX, e.clientY)} onMouseLeave={() => onHover(null)}>
              <span className="dot" style={{ background: colorOf(n) }} />{n.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
