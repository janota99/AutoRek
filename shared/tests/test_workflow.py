"""The shared workflow components: step states, change detection, and the markup each tool draws.

Display only, so these pin wording and escaping, not accounting: a blocker must always be named,
an unchanged figure must never be reported as a change, and nothing a tool passes in is rendered
as markup.
"""
import pytest

from shared import workflow as wf


def test_step_states_mark_earlier_steps_complete_and_later_ones_todo():
    assert wf.step_states(4, 0) == ["active", "todo", "todo", "todo"]
    assert wf.step_states(4, 2) == ["complete", "complete", "active", "todo"]
    assert wf.step_states(3, 3) == ["complete", "complete", "complete"]


def test_changed_inputs_names_each_changed_added_and_removed_input():
    previous = {"Source file": "a", "Mapping file": "b", "Padding": "right"}
    current = {"Source file": "a2", "Mapping file": "b", "Padding": "right", "Excluded IDs": "1"}
    assert wf.changed_inputs(previous, current) == ["Source file", "Excluded IDs"]
    assert wf.changed_inputs(previous, {"Source file": "a"}) == ["Mapping file", "Padding"]
    assert wf.changed_inputs(previous, previous) == []


def test_changes_since_reports_only_figures_that_moved_with_a_signed_delta():
    labels = {"rows": "Rows kept", "total": "Retained total", "same": "Unchanged"}
    lines = wf.changes_since(
        {"rows": 1203, "total": 100.0, "same": 5},
        {"rows": 1210, "total": 99.5, "same": 5}, labels)
    assert lines == ["Rows kept: 1,203 → 1,210 (+7)", "Retained total: 100.00 → 99.50 (-0.50)"]


def test_float_noise_below_half_a_cent_is_not_a_change():
    assert wf.changes_since({"t": 10.0}, {"t": 10.004}, {"t": "Total"}) == []
    assert wf.changes_since({"t": 10.0}, {"t": 10.01}, {"t": "Total"}) != []


def test_changes_since_uses_the_formatter_for_the_value_and_the_delta():
    money = lambda v: f"${v:,.2f}"  # noqa: E731
    lines = wf.changes_since({"je": 100.0}, {"je": 250.5}, {"je": "Proposed JE"}, {"je": money})
    assert lines == ["Proposed JE: $100.00 → $250.50 (+$150.50)"]


def test_text_statuses_are_compared_as_is_and_missing_figures_are_skipped():
    labels = {"status": "Controls", "gone": "Gone"}
    assert wf.changes_since({"status": "PASS"}, {"status": "FAIL"}, labels) == ["Controls: PASS → FAIL"]
    assert wf.changes_since({"status": "PASS"}, {"status": "PASS"}, labels) == []
    assert wf.changes_since({}, {"status": "PASS"}, labels) == []


def test_the_strip_marks_the_active_step_for_assistive_tech():
    html = wf.step_strip_html([("Upload", "2 files"), ("Validate", "Needs fixes"), ("Export", "Locked")], 1)
    assert html.count('data-state="complete"') == 1
    assert html.count('aria-current="step"') == 1
    assert "✓" in html and "Needs fixes" in html


def test_a_blocked_panel_lists_every_blocker_and_a_ready_panel_none():
    blocked = wf.status_html(["Add the source file", "Cache a trial balance"], "Ready")
    assert 'data-tone="blocked"' in blocked
    assert "2 things are blocking the next step" in blocked
    assert "<li>Add the source file</li>" in blocked and "<li>Cache a trial balance</li>" in blocked
    one = wf.status_html(["Only this"], "Ready")
    assert "Blocking the next step" in one and "things are blocking" not in one
    ready = wf.status_html([], "Ready to download", next_step="Open the Downloads tab.")
    assert 'data-tone="ready"' in ready and "Ready to download" in ready and "<li>" not in ready


def test_an_empty_blocker_list_is_always_ready_even_with_a_fail_tone():
    assert 'data-tone="ready"' in wf.status_html([], "Ready", tone="fail")
    assert 'data-tone="fail"' in wf.status_html(["Control failed"], "Ready", tone="fail")
    assert 'data-tone="blocked"' in wf.status_html(["x"], "Ready", tone="unknown")


def test_the_change_panel_is_empty_with_no_changes_and_counts_them():
    assert wf.changes_html([]) == ""
    html = wf.changes_html(["A: 1 → 2 (+1)"], title="Since the previous run")
    assert "Since the previous run: 1 change<" in html
    assert "2 changes" in wf.changes_html(["a", "b"])


@pytest.mark.parametrize("render", [
    lambda text: wf.status_html([text], text, next_step=text),
    lambda text: wf.status_html([], text, next_step=text),
    lambda text: wf.changes_html([text], title=text),
    lambda text: wf.headline_html(text, text, text),
    lambda text: wf.step_strip_html([(text, text)], 0, label=text),
])
def test_nothing_a_tool_passes_in_is_rendered_as_markup(render):
    html = render("<script>alert(1)</script>")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_headline_falls_back_to_neutral_for_an_unknown_tone():
    assert 'data-tone="neutral"' in wf.headline_html("12", "rows", tone="loud")
    assert 'data-tone="bad"' in wf.headline_html("12", "rows", "One sentence.", tone="bad")


def test_workflow_html_combines_strip_status_and_changes():
    html = wf.workflow_html([("A", "x"), ("B", "y")], 0, ["Fix it"], "Ready", changes=["c: 1 → 2 (+1)"])
    assert html.index("pp-wf-steps") < html.index("pp-wf-status") < html.index("pp-wf-changes")
