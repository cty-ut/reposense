"""Agent loop and plan verification, driven by a scripted model (no network, no API key)."""

from conftest import FakeGitHub, ScriptedClient

from reposense.agent import AgentConfig, ContributionAgent

READ_CORE = ("read", {"path": "src/pkg/core.py", "start_line": 1, "end_line": 7, "reason": "look at metavar"})
GOOD_CHANGE = {"file": "src/pkg/core.py", "start_line": 5, "end_line": 6, "anchor": "if self.nargs != 1:",
               "what": "also append ... when multiple=True", "why": "only place adding ...", "confidence": "high"}


def make_agent(ws, rounds, **cfg):
    events = []
    agent = ContributionAgent(ws, AgentConfig(**cfg), emit=events.append, client=ScriptedClient(rounds))
    agent.gh = FakeGitHub()
    return agent, events


def submit(**plan):
    return ("submit_plan", {"summary": "s", "changes": [GOOD_CHANGE], **plan})


def test_happy_path_produces_verified_plan_with_history(ws):
    agent, events = make_agent(ws, [
        [READ_CORE, ("get_pr", {"number": 42})],
        [submit(related_prs=[{"number": 42, "relevance": "added this branch", "review_notes": ["add a test"]}],
                reviewers=[{"login": "erin", "why": "reviewed #42"}])],
    ])
    result = agent.run(7)
    plan = result["plan"]
    assert result["status"] == "ok" and result["run"]["stop_reason"] == "submitted"
    assert plan["changes"] == [GOOD_CHANGE] and plan["dropped"] == []
    assert plan["related_prs"][0]["url"] == "https://github.com/acme/pkg/pull/42"
    assert any(b["pr"] == 42 for b in result["history"]["src/pkg/core.py"])
    assert result["run"]["cached_tokens"] == 1200
    assert [e["type"] for e in events][0] == "start" and events[-1]["type"] == "result"


def test_verifier_rejects_unread_lines_bad_anchor_unknown_pr_and_reviewer(ws):
    fake = {**GOOD_CHANGE, "anchor": "def does_not_exist():"}
    agent, events = make_agent(ws, [
        [submit(related_prs=[{"number": 99, "relevance": "x"}], reviewers=[{"login": "mallory", "why": "x"}])],
        [READ_CORE],
        [("submit_plan", {"summary": "s", "changes": [fake]})],
        [submit()],
    ])
    result = agent.run(7)
    rejected = [e["errors"] for e in events if e["type"] == "plan_rejected"]
    assert any("never read these lines" in err for err in rejected[0])
    assert any("#99" in err for err in rejected[0]) and any("mallory" in err for err in rejected[0])
    assert any("anchor" in err for err in rejected[1])
    assert result["plan"]["changes"] == [GOOD_CHANGE]  # third submission passes
    assert result["run"]["plan_rejections"] == 2


def test_after_max_rejections_invalid_items_are_dropped_not_trusted(ws):
    bad = {**GOOD_CHANGE, "anchor": "nope"}
    agent, _ = make_agent(ws, [[READ_CORE]] + [[("submit_plan", {"summary": "s", "changes": [GOOD_CHANGE, bad]})]] * 3)
    plan = agent.run(7)["plan"]
    assert plan["changes"] == [GOOD_CHANGE]
    assert len(plan["dropped"]) == 1


def test_unknown_arguments_are_reported_to_the_model(ws):
    agent, events = make_agent(ws, [[("read", {"path": "src/pkg/core.py", "offset": 3})]], max_steps=1)
    agent.run(7)
    results = [e for e in events if e["type"] == "tool_result"]
    assert "Unknown argument" in results[0]["summary"] and "start_line" in results[0]["summary"]


def test_step_limit_returns_incomplete(ws):
    agent, _ = make_agent(ws, [[READ_CORE]] * 10, max_steps=3)
    result = agent.run(7)
    assert result["status"] == "incomplete" and result["plan"] is None
    assert result["run"]["stop_reason"] == "max_steps"


def test_rejection_on_last_round_gets_a_grace_round_with_limited_tools(ws):
    client_rounds = [[READ_CORE], [("submit_plan", {"summary": "s", "changes": [{**GOOD_CHANGE, "anchor": "x"}]})],
                     [submit()]]
    agent, _ = make_agent(ws, client_rounds, max_steps=2)
    result = agent.run(7)
    assert result["status"] == "ok" and result["run"]["steps"] == 3
    last_tools = {t["function"]["name"] for t in agent.client.requests[-1]["tools"]}
    assert last_tools == {"read", "get_pr", "submit_plan"}


def test_grace_rounds_are_bounded(ws):
    bad = ("submit_plan", {"summary": "s", "changes": [{**GOOD_CHANGE, "anchor": "x"}]})
    agent, _ = make_agent(ws, [[READ_CORE], [bad], [READ_CORE], [READ_CORE], [READ_CORE], [READ_CORE]], max_steps=2)
    result = agent.run(7)
    assert result["status"] == "incomplete" and result["run"]["steps"] == 4  # 2 steps + 2 grace rounds


def test_identical_call_is_not_repeated(ws):
    grep = ("grep", {"pattern": "nothing-matches-this"})
    agent, events = make_agent(ws, [[grep], [grep], [READ_CORE], [submit()]])
    result = agent.run(7)
    results = [e["summary"] for e in events if e["type"] == "tool_result"]
    assert "duplicate_call" in results[1]
    assert result["status"] == "ok"


def test_history_tools_hidden_in_baseline_mode(ws):
    agent, _ = make_agent(ws, [[READ_CORE], [submit()]], use_history=False)
    result = agent.run(7)
    names = {t["function"]["name"] for t in agent.client.requests[0]["tools"]}
    assert "git_blame" not in names and "get_pr" not in names
    assert result["history"] == {}
