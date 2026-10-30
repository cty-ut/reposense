// Repository map page: breadcrumb navigation, map/graph switch, the active view and the details sidebar.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Dependencies, FromMapView, Load, MapNode, RepoStructure, ToMapView } from '../../shared/protocol';
import { Loadable } from '../components/ui';
import { compact } from '../format';
import { post, useHostMessages } from '../vscodeApi';
import { DependencyGraph } from './DependencyGraph';
import { DependencyIndex } from './graphLayout';
import { MapSidebar } from './MapSidebar';
import { ColorMode, Relations, Treemap } from './Treemap';

const send = (m: FromMapView) => post(m);
type View = 'map' | 'graph';

export function MapApp() {
  const [load, setLoad] = useState<Load<RepoStructure>>({ state: 'loading', message: '正在准备…' });
  const [deps, setDeps] = useState<Load<Dependencies>>({ state: 'idle' });
  const [stack, setStack] = useState<MapNode[]>([]);
  const [view, setView] = useState<View>('map');
  const [mode, setMode] = useState<ColorMode>('size');
  const [activePath, setActivePath] = useState<string | null>(null);
  const [hover, setHover] = useState<{ node: MapNode; x: number; y: number } | null>(null);
  // The sidebar keeps the last hovered node after the mouse leaves the map, so its buttons stay reachable.
  const [inspected, setInspected] = useState<MapNode | null>(null);
  useEffect(() => setInspected(null), [stack, view]);

  useHostMessages(useCallback((m: ToMapView) => {
    if (m.type === 'structure') {
      setLoad(m.value);
      if (m.value.state === 'ready') setStack([m.value.data.root]);
    } else if (m.type === 'dependencies') {
      setDeps(m.value);
    } else if (m.type === 'activeFile') {
      setActivePath(m.path);
    }
  }, []));

  useEffect(() => send({ type: 'ready' }), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 'Escape' || e.key === 'Backspace') && stack.length > 1) setStack((s) => s.slice(0, -1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [stack.length]);

  const layerKinds = useMemo(() => {
    const kinds: Record<string, string> = {};
    if (load.state === 'ready') load.data.root.children?.forEach((l) => { if (l.layer) kinds[l.layer] = l.layer_kind ?? 'other'; });
    return kinds;
  }, [load]);

  const index = useMemo(() => (deps.state === 'ready' ? new DependencyIndex(deps.data.edges) : null), [deps]);

  const relations = useMemo<Relations | null>(() => {
    if (!index || inspected?.kind !== 'file') return null;
    const p = inspected.path;
    return {
      path: p,
      uses: new Set(index.uses(p).map((r) => r.path)),
      usedBy: new Set([...index.usedBy(p), ...index.testedBy(p)].map((r) => r.path)),
    };
  }, [index, inspected]);

  const zoom = (node: MapNode) => {
    if (node.kind === 'file' || !node.children?.length) return;
    setStack((s) => [...s, node]);  // single-directory chains are folded by the views themselves
  };
  const openFile = (path: string) => send({ type: 'openFile', path });
  const onHover = (node: MapNode | null, x?: number, y?: number) => {
    setHover(node ? { node, x: x!, y: y! } : null);
    if (node) setInspected(node);
  };

  return (
    <Loadable load={load} onRetry={() => send({ type: 'refresh' })} render={(structure) => {
      const focus = stack[stack.length - 1] ?? structure.root;
      return (
        <div className="map-app">
          <header className="map-top">
            <span className="map-title">{structure.repo}</span>
            <nav className="crumbs grow">
              {stack.map((n, i) => (
                <span key={n.id} className="row" style={{ gap: 2 }}>
                  {i > 0 && <span className="muted">›</span>}
                  <button className={`crumb${i === stack.length - 1 ? ' current' : ''}`} onClick={() => setStack(stack.slice(0, i + 1))}>
                    {i === 0 ? '全部' : n.name}
                  </button>
                </span>
              ))}
            </nav>
            {view === 'map' && (
              <div className="seg" title="方块大小始终代表代码行数">
                <button className={mode === 'size' ? 'on' : ''} onClick={() => setMode('size')}>按架构层着色</button>
                <button className={mode === 'churn' ? 'on' : ''} onClick={() => setMode('churn')}>改动热度</button>
              </div>
            )}
            <div className="seg">
              <button className={view === 'map' ? 'on' : ''} onClick={() => setView('map')}>地图</button>
              <button className={view === 'graph' ? 'on' : ''} onClick={() => setView('graph')}>关系图</button>
            </div>
            <button className="btn icon" title="重新分析" onClick={() => send({ type: 'refresh' })}>⟳</button>
          </header>
          <div className="map-body">
            {view === 'map' ? (
              <Treemap focus={focus} layerKinds={layerKinds} mode={mode} activePath={activePath} relations={relations}
                       onZoom={zoom} onOpenFile={openFile} onHover={onHover} />
            ) : (
              <DependencyGraph focus={focus} layerKinds={layerKinds} index={index}
                               onZoom={zoom} onOpenFile={openFile} onHover={onHover} />
            )}
            <MapSidebar structure={structure} node={inspected ?? focus} layerKinds={layerKinds} deps={deps} index={index}
                        onZoom={zoom} onOpenFile={openFile} />
          </div>
          {hover && (
            <div className="tooltip" style={{ left: Math.min(hover.x + 14, window.innerWidth - 330), top: hover.y + 14 }}>
              <b>{hover.node.name}</b>
              {hover.node.description && <div>{hover.node.description}</div>}
              <div className="muted">
                {compact(hover.node.lines)} 行 · {hover.node.files} 个文件 · 近一年 {hover.node.churn} 次改动
              </div>
              <div className="muted small">{hover.node.kind === 'file' ? '点击打开文件' : '点击放大'}</div>
            </div>
          )}
        </div>
      );
    }} />
  );
}
