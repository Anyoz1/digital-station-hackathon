import pytest

from digital_station.efficiency import actual, append_sample, metric_sample, supplementary
from digital_station.scenario import DEFAULT_CONFIG, make_initial_state
from digital_station.simulator import Simulator
from digital_station.smoke_plan import load_smoke_plan


def recorded(until):
    initial = make_initial_state()
    engine = Simulator(load_smoke_plan(initial), initial)
    samples = [metric_sample(engine.state)]
    for batch in engine.advance(until):
        samples = append_sample(samples, metric_sample(batch.state))
    return engine.state, samples


def factors(index):
    return {f.key: f for f in index.factors}


def test_actual_uses_engine_transitions_known_physical_integrals_and_due_cohort():
    state, samples = recorded(900)
    index = actual(state, samples, DEFAULT_CONFIG)
    values = factors(index)
    assert index.mode == "actual" and index.formula_version == "efficiency-v1"
    assert (index.window_start_sim_s, index.window_end_sim_s) == (0, 900)
    # R1: T1 from120 through360; R3: T2 from300; R2: T3 from720.
    assert values["occupancy"].raw == pytest.approx((240 + 600 + 180) / 9000)
    assert values["throughput"].raw == 1 and values["delay"].raw == 0
    # L1 and SH1 wait300s each after inspection; ready-demand time720+660.
    assert values["resource_idle"].raw == pytest.approx(600 / 1380)
    assert values["conflicts"].raw == 0
    assert index.score == 95.7 and index.category == "normal"
    assert index.score == round(100 - sum(f.contribution for f in index.factors), 1)
    extra = supplementary(state, samples, 0)
    assert extra["trains_per_hour"] == 4
    assert extra["resource_busy_percent"]["I1"] is not None


def test_empty_duration_is_null_not_perfect_score():
    state = make_initial_state()
    index = actual(state, [metric_sample(state)], DEFAULT_CONFIG)
    assert index.score is None and index.category is None
    assert all(f.raw is None and f.norm_penalty is None and f.contribution is None for f in index.factors)


def test_no_cohort_excludes_weights_and_one_busy_inspector_does_not_penalize_other():
    state, samples = recorded(120)
    pools = samples[-1]["pools"]
    assert pools["inspection"] == {"available": 2, "busy": 1, "demand": 1}
    values = factors(actual(state, samples, DEFAULT_CONFIG))
    assert values["throughput"].raw is None and values["delay"].contribution is None
    assert values["resource_idle"].raw == 0


def test_repeated_same_time_and_sampling_rate_do_not_change_integrals():
    state, samples = recorded(900)
    expanded = []
    for sample in samples:
        expanded.extend([sample, sample, sample])
    assert actual(state, expanded, DEFAULT_CONFIG) == actual(state, samples, DEFAULT_CONFIG)
    kept = samples
    for sample in samples[-1:]:
        kept = append_sample(kept, sample, 0)
    assert kept == samples


def test_rolling_window_excludes_departed_before_start_and_no_activity_returns_null():
    state, samples = recorded(7200)
    index = actual(state, samples, DEFAULT_CONFIG)
    assert index.window_start_sim_s == 6300
    assert all(t.actual_departure_sim_s <= 6300 for t in state.trains)
    assert index.score is None and index.category is None
    assert factors(index)["throughput"].raw is None


def test_changed_weights_thresholds_use_same_actual_facts_and_zero_weight_is_valid():
    state, samples = recorded(900)
    config = {
        **DEFAULT_CONFIG,
        "weights": {k: float(k == "resource_idle") for k in DEFAULT_CONFIG["weights"]},
        "category_thresholds": {"normal_min": 90, "attention_min": 60},
    }
    index = actual(state, samples, config)
    assert index.score == 56.5 and index.category == "critical"
    assert factors(index)["resource_idle"].contribution == pytest.approx(100 * 600 / 1380)
