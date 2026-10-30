// File Insights page: holds the state for the active file and switches between the three tabs.
import { useCallback, useEffect, useState } from 'react';
import { CommitDetail, FileHistory, FileInsight, FileTab, FromFileView, Load, Opportunities, ToFileView } from '../../shared/protocol';
import { Loadable } from '../components/ui';
import { basename } from '../format';
import { post, useHostMessages } from '../vscodeApi';
import { ContributeTab, PlanState } from './ContributeTab';
import { HistoryTab } from './HistoryTab';
import { InsightTab } from './InsightTab';

const send = (m: FromFileView) => post(m);
const idle = { state: 'idle' } as const;

export function FileApp() {
  const [path, setPath] = useState<string | null>(null);
  const [tab, setTab] = useState<FileTab>('history');
  const [history, setHistory] = useState<Load<FileHistory>>(idle);
  const [insight, setInsight] = useState<Load<FileInsight>>(idle);
  const [opps, setOpps] = useState<Load<Opportunities>>(idle);
  const [details, setDetails] = useState<Record<string, Load<CommitDetail>>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [plan, setPlan] = useState<PlanState | null>(null);

  useHostMessages(useCallback((m: ToFileView) => {
    switch (m.type) {
      case 'file':
        setPath(m.path);
        setHistory(idle); setInsight(idle); setOpps(idle); setDetails({}); setSelected(null);
        break;
      case 'history': setHistory(m.value); break;
      case 'insight': setInsight(m.value); break;
      case 'opportunities': setOpps(m.value); break;
      case 'detail': setDetails((d) => ({ ...d, [m.sha]: m.value })); break;
      case 'plan': setPlan({ number: m.number, value: m.value, events: m.events }); setTab('contribute'); break;
      case 'focus': setSelected(m.sha); setTab('history'); break;
      case 'tab': setTab(m.tab); break;
    }
  }, []));

  useEffect(() => send({ type: 'ready' }), []);

  const select = (sha: string | null) => {
    setSelected(sha);
    send({ type: 'focusCommit', sha });
    if (sha && !details[sha]) send({ type: 'loadDetail', sha });
  };
  const open = (url: string) => send({ type: 'openExternal', url });
  const reveal = (p: string, line: number, endLine?: number) => send({ type: 'reveal', path: p, line, endLine });

  if (!path) {
    return (
      <div className="file-app">
        <div className="state">
          <p>在编辑器里打开仓库中的一个文件，这里会显示它的改动历史、功能分析和贡献机会。</p>
          <button className="btn primary" onClick={() => send({ type: 'openMap' })}>打开仓库地图</button>
        </div>
      </div>
    );
  }

  return (
    <div className="file-app">
      <div className="file-head">
        <div className="row">
          <span className="name grow">{basename(path)}</span>
          <button className="btn icon" title="仓库地图" onClick={() => send({ type: 'openMap' })}>⊞</button>
        </div>
        <div className="muted small mono ellipsis" title={path}>{path}</div>
      </div>
      <div className="tabs">
        <button className={`tab${tab === 'history' ? ' active' : ''}`} title="改动历史" onClick={() => setTab('history')}>历史</button>
        <button className={`tab${tab === 'insight' ? ' active' : ''}`} title="功能分析" onClick={() => setTab('insight')}>分析</button>
        <button className={`tab${tab === 'contribute' ? ' active' : ''}`} title="贡献机会" onClick={() => setTab('contribute')}>贡献</button>
      </div>
      {tab === 'history' && (
        <Loadable load={history} render={(h) => (
          <HistoryTab history={h} selected={selected} details={details} onSelect={select} onOpen={open} />
        )} />
      )}
      {tab === 'insight' && (
        <Loadable load={insight} onRetry={() => send({ type: 'loadInsight' })} render={(i) => (
          <InsightTab insight={i} onReveal={(p, l) => reveal(p, l)} onRefresh={() => send({ type: 'loadInsight', refresh: true })} />
        )} />
      )}
      {tab === 'contribute' && (
        <ContributeTab path={path} opportunities={opps} plan={plan}
          onFind={(refresh) => send({ type: 'loadOpportunities', refresh })}
          onPlan={(n) => send({ type: 'planIssue', number: n })}
          onReveal={reveal} onOpen={open} />
      )}
    </div>
  );
}
