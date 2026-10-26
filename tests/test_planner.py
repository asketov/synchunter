from synchunter.config import Scenario
from synchunter.explorer import diff_state
from synchunter.models import Action
from synchunter.planner import find_conflicts, interleavings


def make_scenario(**overrides) -> Scenario:
    values = dict(
        name="t", base_url="http://x", description="", users={"A": "/", "B": "/"},
        reset=None, state=None, observables=[], invariants=[],
        sequential_gap_ms=800, offsets_ms=[0, 50, 120],
    )
    values.update(overrides)
    return Scenario(**values)


def test_diff_state_reports_changed_keys_and_entity_ids():
    before = {"pairs": [{"id": 1, "members": ["A", "B"]}], "answers": [], "question": {"id": 7}}
    after = {"pairs": [{"id": 1, "members": ["A", "B"]}], "answers": [{"id": 100, "pair_id": 1, "user": "A"}],
             "question": {"id": 7}}
    keys, ids = diff_state(before, after)
    assert keys == ["answers"]
    assert ids == ["1", "100"]


def test_diff_state_of_removed_pair_includes_its_id():
    keys, ids = diff_state({"pairs": [{"id": 1}]}, {"pairs": []})
    assert keys == ["pairs"] and ids == ["1"]


def test_conflicts_pair_writes_of_different_users_and_rank_by_overlap():
    submit_a = Action("A", "submit", "Submit", ["POST /api/answer"], ["answers"], ["1", "100"])
    submit_b = Action("B", "submit", "Submit", ["POST /api/answer"], ["answers"], ["1", "101"])
    leave_a = Action("A", "leave", "Leave", ["POST /api/pair/leave"], ["pairs"], ["1"])
    read_b = Action("B", "refresh", "Refresh", [], [], [])
    conflicts = find_conflicts([submit_a, leave_a, submit_b, read_b])

    ids = [c.id for c in conflicts]
    assert "A:submit <> B:submit" in ids and "A:leave <> B:submit" in ids
    assert all("refresh" not in i for i in ids)  # read-only actions never conflict
    assert all(c.a.user != c.b.user for c in conflicts)
    assert conflicts[0].id == "A:submit <> B:submit"  # shared state key + id + endpoint
    assert conflicts[0].score == 4


def test_sequential_strategy_is_turn_taking_only():
    a = Action("A", "x", "X", ["POST /x"])
    b = Action("B", "y", "Y", ["POST /y"])
    conflict = find_conflicts([a, b])[0]
    plans = interleavings(1, conflict, make_scenario(), "sequential")
    assert [p.kind for p in plans] == ["sequential", "sequential"]
    assert [s.delay_ms for s in plans[0].steps] == [0, 800]


def test_interleave_strategy_adds_both_directions_for_each_nonzero_offset():
    a = Action("A", "x", "X", ["POST /x"])
    b = Action("B", "y", "Y", ["POST /y"])
    conflict = find_conflicts([a, b])[0]
    plans = interleavings(3, conflict, make_scenario(), "interleave")
    # 2 sequential + offset 0 once + offsets 50 and 120 in both directions
    assert len(plans) == 2 + 1 + 4
    assert plans[0].id == "3.1"
    assert sum(p.kind == "concurrent" for p in plans) == 5
