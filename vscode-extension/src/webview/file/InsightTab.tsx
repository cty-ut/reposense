// Analysis tab: what the file does, its key symbols (click to jump), collaborators, tips.
import { FileInsight } from '../../shared/protocol';
import { SectionTitle } from '../components/ui';

const KIND_SHORT: Record<string, string> = { class: 'class', function: 'fn', method: 'method', constant: 'const' };

export function InsightTab({ insight, onReveal, onRefresh }: {
  insight: FileInsight;
  onReveal: (path: string, line: number) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="pane">
      <SectionTitle right={<button className="btn icon small" title="重新分析" onClick={onRefresh}>⟳</button>}>概要</SectionTitle>
      <div>{insight.summary}</div>
      {insight.role && <div className="muted" style={{ marginTop: 6 }}>{insight.role}</div>}

      {insight.responsibilities.length > 0 && (
        <>
          <SectionTitle>主要职责</SectionTitle>
          <ul className="tight">{insight.responsibilities.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>
      )}

      {insight.symbols.length > 0 && (
        <>
          <SectionTitle>关键符号</SectionTitle>
          <div className="list" style={{ gap: 0 }}>
            {insight.symbols.map((s) => (
              <div key={`${s.name}:${s.line}`} className="symbol" onClick={() => onReveal(insight.path, s.line)} title={`跳到第 ${s.line} 行`}>
                <span className="kind-chip">{KIND_SHORT[s.kind] ?? s.kind}</span>
                <span className="row"><code className="grow ellipsis">{s.name}</code><span className="muted small">L{s.line}</span></span>
                <span />
                <span className="muted small">{s.description}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {insight.collaborators.length > 0 && (
        <>
          <SectionTitle>相关文件</SectionTitle>
          {insight.collaborators.map((c) => (
            <div key={c.path} className="hotfile" onClick={() => onReveal(c.path, 1)}>
              <code className="grow ellipsis">{c.path}</code>
              <span className="muted small">{c.relation}</span>
            </div>
          ))}
        </>
      )}

      {insight.newcomer_tips.length > 0 && (
        <>
          <SectionTitle>修改前须知</SectionTitle>
          <ul className="tight">{insight.newcomer_tips.map((t, i) => <li key={i}>{t}</li>)}</ul>
        </>
      )}

      {insight.dropped.length > 0 && (
        <details className="small muted" style={{ marginTop: 12 }}>
          <summary>校验时丢弃了 {insight.dropped.length} 条不准确的内容</summary>
          <ul className="tight">{insight.dropped.map((d, i) => <li key={i}>{d}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
