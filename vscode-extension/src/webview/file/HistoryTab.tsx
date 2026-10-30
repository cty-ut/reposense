// History tab: ownership bar, git-graph style timeline of recent commits, expandable commit details.
import { commitColor } from '../../shared/palette';
import { CommitDetail, FileCommit, FileHistory, Load } from '../../shared/protocol';
import { Link, Loadable, SectionTitle } from '../components/ui';
import { relativeDate } from '../format';

interface Props {
  history: FileHistory;
  selected: string | null;
  details: Record<string, Load<CommitDetail>>;
  onSelect: (sha: string | null) => void;
  onOpen: (url: string) => void;
}

export function HistoryTab({ history, selected, details, onSelect, onOpen }: Props) {
  if (!history.commits.length) return <div className="state">这个文件还没有提交记录。</div>;
  const owned = history.commits.reduce((s, c) => s + c.owned_lines, 0);
  return (
    <div className="pane">
      <OwnershipBar history={history} />
      <div className="muted small">
        最近 {history.commits.length} 次改动仍保留在当前文件的 {owned}/{history.total_lines} 行中；
        代码里的彩色边框对应下面的提交。
      </div>

      <SectionTitle>改动时间线</SectionTitle>
      <div className="graph">
        {history.commits.map((c) => (
          <div key={c.sha}>
            <CommitRow commit={c} selected={selected === c.sha} onClick={() => onSelect(selected === c.sha ? null : c.sha)} onOpen={onOpen} />
            {selected === c.sha && (
              <div className="detail">
                <Loadable load={details[c.sha] ?? { state: 'loading' }} render={(d) => <CommitDetailView detail={d} onOpen={onOpen} />} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function OwnershipBar({ history }: { history: FileHistory }) {
  const total = Math.max(history.total_lines, 1);
  return (
    <div className="ownbar" title="当前文件的每一行最后由哪次提交修改">
      {history.commits.filter((c) => c.owned_lines > 0).map((c) => (
        <div key={c.sha} title={`${c.subject} · ${c.owned_lines} 行`}
             style={{ width: `${(c.owned_lines / total) * 100}%`, background: commitColor(c.index) }} />
      ))}
    </div>
  );
}

function CommitRow({ commit: c, selected, onClick, onOpen }: {
  commit: FileCommit; selected: boolean; onClick: () => void; onOpen: (url: string) => void;
}) {
  return (
    <div className={`commit${selected ? ' selected' : ''}`} onClick={onClick}>
      <div className="lane"><span className="node" style={{ background: commitColor(c.index) }} /></div>
      <div className="commit-body">
        <div className="commit-subject clamp2">{c.subject}</div>
        <div className="meta">
          <span>{c.author}</span>
          <span title={c.date}>{relativeDate(c.date)}</span>
          <span><span className="add">+{c.additions}</span> <span className="del">−{c.deletions}</span></span>
          {c.pr && <span className="badge"><Link href={c.pr.url} onOpen={onOpen}>PR #{c.pr.number}</Link></span>}
          {c.owned_lines > 0
            ? <span className="badge" title="当前文件中最后由这次提交修改的行数">{c.owned_lines} 行</span>
            : <span className="badge" title="这次提交的改动已被后来的提交覆盖">已被覆盖</span>}
        </div>
      </div>
    </div>
  );
}

function CommitDetailView({ detail: d, onOpen }: { detail: CommitDetail; onOpen: (url: string) => void }) {
  const fileComments = d.pr?.review_comments.filter((c) => c.path === d.path) ?? [];
  return (
    <>
      <div className="row small">
        <Link href={d.url} onOpen={onOpen}>{d.sha.slice(0, 8)}</Link>
        <span className="muted">{d.author} · {d.date}</span>
      </div>
      {d.body && <div className="prebody small muted">{d.body}</div>}

      {d.pr && (
        <div className="card">
          <div className="row">
            <span className="badge solid" style={{ background: d.pr.state === 'merged' ? '#8957e5' : '#238636' }}>{d.pr.state}</span>
            <Link href={d.pr.url} onOpen={onOpen}><b>#{d.pr.number} {d.pr.title}</b></Link>
          </div>
          <div className="muted small">@{d.pr.author}{d.pr.merged_at ? ` · 合并于 ${d.pr.merged_at}` : ''} · 共改了 {d.pr.files.length} 个文件</div>
          {d.pr.body && <details><summary className="small muted">PR 描述</summary><div className="prebody small">{d.pr.body}</div></details>}
          {(d.pr.reviews.length > 0 || fileComments.length > 0) && <div className="section-title">Review 意见</div>}
          <div className="list">
            {fileComments.map((c, i) => (
              <div key={`c${i}`} className="review">
                <div className="who">@{c.reviewer} <span className="muted small">评论了这个文件{c.line ? ` 第 ${c.line} 行` : ''}</span></div>
                <div className="prebody small">{c.body}</div>
              </div>
            ))}
            {d.pr.reviews.map((r, i) => (
              <div key={`r${i}`} className="review">
                <div className="who">@{r.reviewer} <span className="muted small">{r.state.toLowerCase()}</span></div>
                <div className="prebody small">{r.body}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      {d.pr_error && <div className="muted small">无法读取 PR：{d.pr_error}</div>}

      <DiffView diff={d.diff} />
    </>
  );
}

function DiffView({ diff }: { diff: string }) {
  const lines = diff.split('\n').filter((l) => !l.startsWith('diff --git') && !l.startsWith('index ') && !l.startsWith('---') && !l.startsWith('+++'));
  if (!lines.join('').trim()) return null;
  return (
    <div className="diff">
      {lines.map((l, i) => (
        <div key={i} className={l.startsWith('@@') ? 'd-hunk' : l.startsWith('+') ? 'd-add' : l.startsWith('-') ? 'd-del' : ''}>{l || ' '}</div>
      ))}
    </div>
  );
}
