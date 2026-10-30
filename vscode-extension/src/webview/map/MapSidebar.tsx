// Right-hand panel of the map: details of the focused/hovered node, layer legend, hottest files.
import { layerColor, USED_BY_COLOR, USES_COLOR } from '../../shared/palette';
import { Dependencies, Load, MapNode, RepoStructure } from '../../shared/protocol';
import { basename, compact } from '../format';
import { DependencyIndex, Related } from './graphLayout';

const KIND_LABEL: Record<MapNode['kind'], string> = { repo: '仓库', layer: '架构层', dir: '目录', file: '文件' };
const LANG_COLORS = ['#4f9cf9', '#f5a524', '#17c964', '#9353d3', '#f31260'];

function hottestFiles(node: MapNode, limit = 6): MapNode[] {
  const files: MapNode[] = [];
  const walk = (n: MapNode) => (n.kind === 'file' ? files.push(n) : n.children?.forEach(walk));
  walk(node);
  return files.filter((f) => f.churn > 0).sort((a, b) => b.churn - a.churn).slice(0, limit);
}

interface Props {
  structure: RepoStructure;
  node: MapNode;
  layerKinds: Record<string, string>;
  deps: Load<Dependencies>;
  index: DependencyIndex | null;
  onZoom: (node: MapNode) => void;
  onOpenFile: (path: string) => void;
}

export function MapSidebar({ structure, node, layerKinds, deps, index, onZoom, onOpenFile }: Props) {
  const kind = node.kind === 'layer' ? node.layer_kind : layerKinds[node.layer ?? ''];
  const langs = Object.entries(node.languages ?? (node.language ? { [node.language]: node.lines } : {}));
  const langTotal = langs.reduce((s, [, n]) => s + n, 0) || 1;
  const hot = hottestFiles(node);

  return (
    <aside className="map-side">
      <div className="row" style={{ gap: 8 }}>
        <span className="dot" style={{ background: layerColor(kind) }} />
        <span className="badge">{KIND_LABEL[node.kind]}</span>
      </div>
      <h3 style={{ marginTop: 6 }}>{node.name}</h3>
      {node.path && <div className="muted small mono" style={{ wordBreak: 'break-all' }}>{node.path}</div>}
      {node.description && <p style={{ margin: '8px 0' }}>{node.description}</p>}

      <div className="stats">
        <div className="stat"><b>{compact(node.lines)}</b><span className="muted small">行代码</span></div>
        <div className="stat"><b>{compact(node.files)}</b><span className="muted small">文件</span></div>
        <div className="stat" title="近 12 个月涉及的提交次数（按文件累计）"><b>{compact(node.churn)}</b><span className="muted small">次改动</span></div>
      </div>

      {langs.length > 0 && (
        <>
          <div className="langbar">
            {langs.map(([lang, n], i) => (
              <div key={lang} style={{ width: `${(n / langTotal) * 100}%`, background: LANG_COLORS[i % LANG_COLORS.length] }} />
            ))}
          </div>
          <div className="row small muted" style={{ flexWrap: 'wrap', gap: 10 }}>
            {langs.map(([lang, n], i) => (
              <span key={lang} className="row" style={{ gap: 4 }}>
                <span className="dot" style={{ background: LANG_COLORS[i % LANG_COLORS.length] }} />
                {lang} {Math.round((n / langTotal) * 100)}%
              </span>
            ))}
          </div>
        </>
      )}

      {node.kind === 'file' && (
        <div style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={() => onOpenFile(node.path)}>打开并分析这个文件</button>
        </div>
      )}

      {node.kind === 'file' && (
        index ? (
          <>
            <RelationList title="它依赖" hint="这个文件 import 的文件" color={USES_COLOR} items={index.uses(node.path)} onOpen={onOpenFile} />
            <RelationList title="被谁依赖" hint="import 了这个文件的代码；改动它可能影响这些文件" color={USED_BY_COLOR}
                          items={index.usedBy(node.path)} onOpen={onOpenFile} />
            <RelationList title="对应的测试" hint="import 了这个文件的测试；改完后先跑这些" color={USED_BY_COLOR}
                          items={index.testedBy(node.path)} onOpen={onOpenFile} />
          </>
        ) : (
          <p className="muted small">{deps.state === 'error' ? `依赖分析失败：${deps.message}` : '正在分析 import 关系…'}</p>
        )
      )}

      {hot.length > 0 && node.kind !== 'file' && (
        <>
          <div className="section-title">最近改动最频繁</div>
          {hot.map((f) => (
            <div key={f.id} className="hotfile" onClick={() => onOpenFile(f.path)} title={f.path}>
              <span className="grow ellipsis">{f.name}</span>
              <span className="badge">{f.churn} 次</span>
            </div>
          ))}
        </>
      )}

      {node.kind === 'repo' && (
        <>
          <div className="section-title">架构层</div>
          <div className="legend">
            {(structure.root.children ?? []).map((layer) => (
              <div key={layer.id} className="legend-item" onClick={() => onZoom(layer)} title={layer.description ?? ''}>
                <span className="dot" style={{ background: layerColor(layer.layer_kind) }} />
                <span className="grow ellipsis">{layer.name}</span>
                <span className="muted small">{compact(layer.lines)} 行</span>
              </div>
            ))}
          </div>
        </>
      )}
    </aside>
  );
}

const SHOWN = 8;

function RelationList({ title, hint, color, items, onOpen }: {
  title: string; hint: string; color: string; items: Related[]; onOpen: (path: string) => void;
}) {
  return (
    <>
      <div className="section-title" title={hint}>{title}（{items.length}）</div>
      {items.length === 0 && <div className="muted small">无</div>}
      <div className="rel-list">
        {items.slice(0, SHOWN).map((r) => (
          <div key={r.path} className="hotfile" onClick={() => onOpen(r.path)} title={r.path}>
            <span className="dot" style={{ background: color }} />
            <span className="grow ellipsis">{basename(r.path)}</span>
            <span className="badge" title="import 语句和引用的次数">{r.weight} 处</span>
          </div>
        ))}
        {items.length > SHOWN && <div className="muted small">还有 {items.length - SHOWN} 个</div>}
      </div>
    </>
  );
}
