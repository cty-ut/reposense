// Contribute tab: related open issues, improvement ideas, and the issue contribution plan.
import { Load, Opportunities, PlanEvent, PlanResult } from '../../shared/protocol';
import { Link, Loadable, SectionTitle } from '../components/ui';
import { relativeDate } from '../format';

export interface PlanState {
  number: number;
  value: Load<PlanResult>;
  events: PlanEvent[];
}

interface Props {
  path: string;
  opportunities: Load<Opportunities>;
  plan: PlanState | null;
  onFind: (refresh?: boolean) => void;
  onPlan: (issue: number) => void;
  onReveal: (path: string, line: number, endLine?: number) => void;
  onOpen: (url: string) => void;
}

export function ContributeTab({ path, opportunities, plan, onFind, onPlan, onReveal, onOpen }: Props) {
  const planning = plan?.value.state === 'loading';
  return (
    <div className="pane">
      {plan && <PlanView plan={plan} onReveal={onReveal} onOpen={onOpen} />}
      <Loadable load={opportunities} onRetry={() => onFind()}
        idle={(
          <div className="state">
            <p>找出和这个文件相关的开放 Issue，并让模型阅读代码，提出适合新手的改进点。</p>
            <button className="btn primary" onClick={() => onFind()}>查找贡献机会</button>
          </div>
        )}
        render={(o) => (
          <>
            <SectionTitle right={<button className="btn icon small" title="重新查找" onClick={() => onFind(true)}>⟳</button>}>
              相关的开放 Issue
            </SectionTitle>
            {o.issues.length === 0 && <div className="muted small">没有找到提到这个文件或其主要符号的开放 Issue。</div>}
            <div className="list">
              {o.issues.map((i) => (
                <div key={i.number} className="card">
                  <Link href={i.url} onOpen={onOpen}><b>#{i.number}</b> {i.title}</Link>
                  <div className="meta">
                    <span>{relativeDate(i.created_at)}</span>
                    <span>💬 {i.comments}</span>
                    <span>{i.match}</span>
                    {i.labels.map((l) => <span key={l} className="badge">{l}</span>)}
                  </div>
                  <button className="btn" style={{ marginTop: 6 }} disabled={planning} onClick={() => onPlan(i.number)}>
                    生成贡献计划
                  </button>
                </div>
              ))}
            </div>

            <SectionTitle>可以从这里入手</SectionTitle>
            <div className="list">
              {o.suggestions.map((s, idx) => (
                <div key={idx} className="card" style={{ cursor: 'pointer' }} onClick={() => onReveal(path, s.start_line, s.end_line)}>
                  <div className="row">
                    <span className={`badge solid diff-${s.difficulty}`}>{({ easy: '简单', medium: '中等', hard: '较难' })[s.difficulty] ?? s.difficulty}</span>
                    <span className="badge">{s.kind}</span>
                    <b className="grow">{s.title}</b>
                  </div>
                  <div className="small" style={{ marginTop: 4 }}>{s.detail}</div>
                  <div className="muted small mono">L{s.start_line}–{s.end_line}</div>
                </div>
              ))}
            </div>
            {o.dropped.length > 0 && (
              <details className="small muted" style={{ marginTop: 8 }}>
                <summary>校验时丢弃了 {o.dropped.length} 条引用不准确的建议</summary>
                <ul className="tight">{o.dropped.map((d, i) => <li key={i}>{d}</li>)}</ul>
              </details>
            )}
          </>
        )} />
    </div>
  );
}

function PlanView({ plan, onReveal, onOpen }: { plan: PlanState; onReveal: Props['onReveal']; onOpen: Props['onOpen'] }) {
  const calls = plan.events.filter((e) => e.type === 'tool_call' || e.type === 'tool_result' || e.type === 'plan_rejected');
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <b>Issue #{plan.number} 的贡献计划</b>
      {plan.value.state !== 'ready' && calls.length > 0 && (
        <div className="events" style={{ marginTop: 6 }}>
          {calls.map((e, i) => (
            <div key={i} className={e.type === 'tool_result' ? (e.ok ? 'ok' : 'bad') : e.type === 'plan_rejected' ? 'bad' : ''}>
              {e.type === 'tool_call' && `${e.step}. ${e.name} — ${e.reason ?? ''}`}
              {e.type === 'tool_result' && `   → ${e.summary}`}
              {e.type === 'plan_rejected' && `   校验未通过：${(e.errors ?? []).join('；')}`}
            </div>
          ))}
        </div>
      )}
      <Loadable load={plan.value} render={(r) => {
        const p = r.plan;
        if (!p) return <div className="muted">没有产出计划（{r.run.stop_reason}）。</div>;
        return (
          <>
            <p>{p.summary}</p>
            {(p.open_questions?.length ?? 0) > 0 && (
              <>
                <div className="section-title">动手前先和维护者确认</div>
                <ul className="tight">{p.open_questions!.map((q, i) => <li key={i}>{q}</li>)}</ul>
              </>
            )}
            <div className="section-title">要改的地方（代码中已用黄色标出）</div>
            {p.changes.map((c, i) => (
              <div key={i} className="hotfile" style={{ display: 'block' }} onClick={() => onReveal(c.file, c.start_line, c.end_line)}>
                <code>{c.file}:{c.start_line}</code> <span className="badge">{c.confidence}</span>
                <div className="small">{c.what}</div>
              </div>
            ))}
            {(p.related_prs?.length ?? 0) > 0 && (
              <>
                <div className="section-title">相关的历史 PR</div>
                {p.related_prs!.map((pr) => (
                  <div key={pr.number} className="small" style={{ marginBottom: 6 }}>
                    <Link href={pr.url} onOpen={onOpen}>#{pr.number} {pr.title}</Link>
                    <div className="muted">{pr.relevance}</div>
                    {pr.review_notes?.length ? <ul className="tight">{pr.review_notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
                  </div>
                ))}
              </>
            )}
            {(p.tests?.length ?? 0) > 0 && (
              <>
                <div className="section-title">测试</div>
                {p.tests!.map((t, i) => <div key={i} className="small"><code>{t.command ?? t.file}</code> <span className="muted">{t.why}</span></div>)}
              </>
            )}
            <div className="muted small" style={{ marginTop: 8 }}>{r.run.steps} 轮 · {r.run.tool_calls} 次工具调用 · {r.run.seconds}s</div>
          </>
        );
      }} />
    </div>
  );
}
