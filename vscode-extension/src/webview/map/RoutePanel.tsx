// Sidebar panel for the newcomer reading order: numbered steps, why each one, where to start reading.
import { Load, ReadingPath } from '../../shared/protocol';
import { Loadable } from '../components/ui';
import { basename } from '../format';

interface Props {
  route: Load<ReadingPath>;
  describe: (path: string) => string | null | undefined;
  onOpen: (path: string, line?: number) => void;
  onRegenerate: () => void;
  onClose: () => void;
}

export function RoutePanel({ route, describe, onOpen, onRegenerate, onClose }: Props) {
  return (
    <aside className="map-side">
      <div className="row">
        <h3 className="grow">新手阅读路线</h3>
        <button className="btn icon" title="关闭" onClick={onClose}>×</button>
      </div>
      <p className="muted small" style={{ margin: '4px 0 10px' }}>
        按顺序读。每一步都和前面某一步有 import 关系；地图和关系图上的数字对应这里的步骤。
      </p>
      <Loadable load={route} onRetry={onRegenerate} render={(r) => (
        <>
          {r.steps.length === 0 && <div className="state">没有生成有效的路线。</div>}
          <ol className="route">
            {r.steps.map((s, i) => (
              <li key={s.path} className="route-step">
                <span className="step-badge">{i + 1}</span>
                <div className="grow" style={{ minWidth: 0 }}>
                  <a className="route-file" onClick={() => onOpen(s.path)} title={s.path}>{basename(s.path)}</a>
                  {describe(s.path) && <div className="muted small">{describe(s.path)}</div>}
                  <div className="route-why">{s.why}</div>
                  {s.look_for.length > 0 && (
                    <div className="row" style={{ flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                      <span className="muted small">先看</span>
                      {s.look_for.map((l) => (
                        <button key={l.name} className="chip mono" title={`跳到第 ${l.line} 行`} onClick={() => onOpen(s.path, l.line)}>
                          {l.name}<span className="muted">:{l.line}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
          <div className="row muted small" style={{ marginTop: 10 }}>
            {r.dropped.length > 0 && (
              <span className="grow" title={r.dropped.join('\n')}>校验丢弃了 {r.dropped.length} 条对不上的模型输出</span>
            )}
            <a onClick={onRegenerate}>重新生成</a>
          </div>
        </>
      )} />
    </aside>
  );
}
