// Zoomable treemap: shows the focused node's children as framed groups and grandchildren as tiles.
import { hierarchy, HierarchyRectangularNode, treemap, treemapSquarify } from 'd3-hierarchy';
import { useEffect, useMemo, useRef, useState } from 'react';
import { layerColor, withAlpha } from '../../shared/palette';
import { MapNode } from '../../shared/protocol';
import { compact } from '../format';

export type ColorMode = 'size' | 'churn';

interface Props {
  focus: MapNode;
  layerKinds: Record<string, string>;
  mode: ColorMode;
  activePath: string | null;
  onZoom: (node: MapNode) => void;
  onOpenFile: (path: string) => void;
  onHover: (node: MapNode | null, x?: number, y?: number) => void;
}

type Rect = HierarchyRectangularNode<MapNode>;

function useSize() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return { ref, ...size };
}

function contains(node: MapNode, path: string | null): boolean {
  if (!path) return false;
  if (node.kind === 'file') return node.path === path;
  if (node.kind === 'layer' || node.kind === 'repo') return !!node.children?.some((c) => contains(c, path));
  return path.startsWith(node.path + '/');
}

export function Treemap({ focus, layerKinds, mode, activePath, onZoom, onOpenFile, onHover }: Props) {
  const { ref, w, h } = useSize();

  const layout = useMemo(() => {
    if (!w || !h) return [] as Rect[];
    const root = hierarchy<MapNode>(focus, (n) => n.children)
      .sum((n) => (n.kind === 'file' ? Math.max(n.lines, 1) : 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return treemap<MapNode>()
      .size([w, h])
      .tile(treemapSquarify.ratio(1.3))
      .paddingInner((d) => (d.depth === 0 ? 6 : 2))
      .paddingTop((d) => (d.depth === 1 && d.children ? 24 : d.depth === 0 ? 0 : 2))
      .paddingOuter((d) => (d.depth === 0 ? 0 : 3))(root)
      .descendants()
      .filter((d) => d.depth === 1 || d.depth === 2) as Rect[];
  }, [focus, w, h]);

  const maxChurn = useMemo(() => Math.max(1, ...layout.map((d) => d.data.churn)), [layout]);

  const colorOf = (n: MapNode) => layerColor(n.kind === 'layer' ? n.layer_kind : layerKinds[n.layer ?? ''] ?? 'other');
  const fill = (n: MapNode) => {
    if (mode === 'churn') {
      const t = Math.log1p(n.churn) / Math.log1p(maxChurn);
      return withAlpha('#f97316', 0.06 + 0.8 * t);
    }
    return withAlpha(colorOf(n), n.kind === 'file' ? 0.22 : 0.34);
  };

  const click = (n: MapNode) => (n.kind === 'file' ? onOpenFile(n.path) : onZoom(n));

  return (
    <div className="map-canvas" ref={ref} onMouseLeave={() => onHover(null)}>
      {layout.map((d) => {
        const n = d.data;
        const style = { left: d.x0, top: d.y0, width: Math.max(0, d.x1 - d.x0), height: Math.max(0, d.y1 - d.y0) };
        const active = contains(n, activePath);
        const hover = {
          onMouseMove: (e: React.MouseEvent) => { e.stopPropagation(); onHover(n, e.clientX, e.clientY); },
          onClick: (e: React.MouseEvent) => { e.stopPropagation(); click(n); },
        };
        if (d.depth === 1 && d.children) {
          const color = colorOf(n);
          return (
            <div key={n.id} className={`tile group${active ? ' active' : ''}`} style={{ ...style, borderColor: color }} {...hover}>
              <div className="tile-head" style={{ background: withAlpha(color, 0.18) }}>
                <span className="ellipsis">{n.name}</span>
                <span className="kind">{compact(n.lines)} 行</span>
              </div>
            </div>
          );
        }
        const showLabel = style.width > 46 && style.height > 18;
        return (
          <div key={n.id} className={`tile leaf${active ? ' active' : ''}`}
               style={{ ...style, background: fill(n), zIndex: d.depth }} {...hover}>
            {showLabel && (
              <div className="tile-label">
                <div className="ellipsis" style={{ fontWeight: n.kind === 'file' ? 400 : 600 }}>
                  {n.kind === 'file' ? '' : '▸ '}{n.name}
                </div>
                {style.height > 34 && <div className="muted ellipsis">{compact(n.lines)} 行{n.churn ? ` · ${n.churn} 次改动` : ''}</div>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
