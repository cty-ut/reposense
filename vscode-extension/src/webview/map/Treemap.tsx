// Zoomable nested treemap: draws up to three levels below the focused node, all the way down to files.
import { hierarchy, HierarchyRectangularNode, treemap, treemapSquarify } from 'd3-hierarchy';
import { useMemo } from 'react';
import { layerColor, withAlpha } from '../../shared/palette';
import { MapNode } from '../../shared/protocol';
import { compact } from '../format';
import { collapse } from './tree';
import { useSize } from './useSize';

export type ColorMode = 'size' | 'churn';

const MAX_DEPTH = 3;
const HEAD = 22;
const HEAT = '#e8590c';

/** The file whose imports are highlighted, and the files on either side of it. */
export interface Relations {
  path: string;
  uses: Set<string>;
  usedBy: Set<string>;
}

interface Props {
  focus: MapNode;
  layerKinds: Record<string, string>;
  mode: ColorMode;
  activePath: string | null;
  relations: Relations | null;
  steps: Map<string, number> | null;  // file path -> step of the reading order
  onZoom: (node: MapNode) => void;
  onOpenFile: (path: string) => void;
  onHover: (node: MapNode | null, x?: number, y?: number) => void;
}

type Rect = HierarchyRectangularNode<MapNode>;

function contains(node: MapNode, path: string | null): boolean {
  if (!path) return false;
  if (node.kind === 'file') return node.path === path;
  if (node.kind === 'layer' || node.kind === 'repo') return !!node.children?.some((c) => contains(c, path));
  return path.startsWith(node.path + '/');
}

const width = (d: Rect) => d.x1 - d.x0;
const height = (d: Rect) => d.y1 - d.y0;
const hasHeader = (d: Rect) => width(d) > 70 && height(d) > 48;

function relationClass(n: MapNode, r: Relations | null): string {
  if (!r) return '';
  if (n.path === r.path) return ' rel-self';
  if (r.uses.has(n.path)) return ' rel-uses';
  if (r.usedBy.has(n.path)) return ' rel-used-by';
  return ' dim';
}

export function Treemap({ focus, layerKinds, mode, activePath, relations, steps, onZoom, onOpenFile, onHover }: Props) {
  const { ref, w, h } = useSize();

  const layout = useMemo(() => {
    if (!w || !h) return [] as Rect[];
    const root = hierarchy<MapNode>(collapse(focus), (n) => n.children?.map(collapse))
      .sum((n) => (n.kind === 'file' ? Math.max(n.lines, 1) : 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    return treemap<MapNode>()
      .size([w, h])
      .round(true)
      .tile(treemapSquarify.ratio(1.2))
      .paddingInner((d) => (d.depth === 0 ? 8 : 3))
      .paddingOuter((d) => (d.depth === 0 ? 0 : 4))
      .paddingTop((d) => (d.depth === 0 ? 0 : hasHeader(d) ? HEAD : 4))(root)
      .descendants()
      .filter((d) => d.depth >= 1 && d.depth <= MAX_DEPTH && width(d) >= 3 && height(d) >= 3) as Rect[];
  }, [focus, w, h]);

  const { maxChurn, maxLines } = useMemo(() => ({
    maxChurn: Math.max(1, ...layout.map((d) => d.data.churn)),
    maxLines: Math.max(1, ...layout.filter((d) => d.data.kind === 'file').map((d) => d.data.lines)),
  }), [layout]);

  const colorOf = (n: MapNode) => layerColor(n.kind === 'layer' ? n.layer_kind : layerKinds[n.layer ?? ''] ?? 'other');
  const tileFill = (n: MapNode) => {
    if (mode === 'churn') return withAlpha(HEAT, 0.04 + 0.76 * (Math.log1p(n.churn) / Math.log1p(maxChurn)));
    // bigger files are a little darker, which gives the map some texture without extra colors
    return withAlpha(colorOf(n), 0.12 + 0.2 * (Math.log1p(n.lines) / Math.log1p(maxLines)));
  };
  const heat = (n: MapNode) => Math.log1p(n.churn) / Math.log1p(maxChurn);

  return (
    <div className="map-canvas" ref={ref} onMouseLeave={() => onHover(null)}>
      {layout.map((d) => {
        const n = d.data;
        const box = { left: d.x0, top: d.y0, width: width(d), height: height(d) };
        const cls = contains(n, activePath) ? ' active' : '';
        const events = {
          onMouseMove: (e: React.MouseEvent) => { e.stopPropagation(); onHover(n, e.clientX, e.clientY); },
          onClick: (e: React.MouseEvent) => { e.stopPropagation(); n.kind === 'file' ? onOpenFile(n.path) : onZoom(n); },
        };

        if (d.children && d.depth < MAX_DEPTH) {
          const color = colorOf(n);
          const top = d.depth === 1;
          const style = mode === 'size' && top
            ? { ...box, background: withAlpha(color, 0.07), borderColor: withAlpha(color, 0.4) }
            : box;
          return (
            <div key={n.id} className={`frame${top ? ' top' : ''}${cls}`} style={style} {...events}>
              {hasHeader(d) && (
                <div className="frame-head">
                  {top && <span className="dot" style={{ background: color }} />}
                  <span className="ellipsis">{n.kind === 'dir' ? `${n.name}/` : n.name}</span>
                  {n.kind === 'layer' && n.path && <span className="muted ellipsis mono">{n.path}/</span>}
                  <span className="grow" />
                  {width(d) > 160 && <span className="muted">{compact(n.lines)} 行</span>}
                </div>
              )}
            </div>
          );
        }

        const folded = n.kind !== 'file';
        const big = box.width > 110 && box.height > 60;
        const border = mode === 'size' ? withAlpha(colorOf(n), 0.35) : 'transparent';
        const step = n.kind === 'file' ? steps?.get(n.path) : undefined;
        return (
          <div key={n.id} className={`leaf${folded ? ' folded' : ''}${cls}${relationClass(n, relations)}${step ? ' on-route' : ''}`}
               style={{ ...box, background: tileFill(n), borderColor: border }} {...events}>
            {box.width > 44 && box.height > 18 && (
              <div className={`leaf-label${big ? ' big' : ''}`}>
                <div className="ellipsis name">{folded ? `${n.name}/` : n.name}</div>
                {box.height > 36 && box.width > 70 && (
                  <div className="muted ellipsis">
                    {mode === 'churn' ? `${n.churn} 次改动` : `${compact(n.lines)} 行`}
                  </div>
                )}
                {big && n.description && box.height > 70 && (
                  <div className="leaf-desc" style={{ WebkitLineClamp: Math.min(4, Math.floor((box.height - 54) / 16)) }}>
                    {n.description}
                  </div>
                )}
              </div>
            )}
            {step && <span className="step-badge">{step}</span>}
            {mode === 'size' && n.churn > 0 && box.width > 24 && box.height > 12 && (
              <div className="heatbar" title={`近一年 ${n.churn} 次改动`}
                   style={{ width: `${Math.max(8, 100 * heat(n))}%`, background: withAlpha(HEAT, 0.35 + 0.5 * heat(n)) }} />
            )}
          </div>
        );
      })}
    </div>
  );
}
