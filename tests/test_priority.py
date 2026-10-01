from app.frontier.priority import compute_priority


def test_priority_is_weighted_average_in_unit_range():
    p = compute_priority(mission_relevance=1, business_probability=1, source_quality=1, novelty=1, information_gain=1)
    assert p == 1.0
    p = compute_priority(mission_relevance=0, business_probability=0, source_quality=0, novelty=0, information_gain=0)
    assert p == 0.0


def test_brain_can_override_weights():
    base = compute_priority(mission_relevance=1, business_probability=0, source_quality=0, novelty=0, information_gain=0)
    tuned = compute_priority(mission_relevance=1, business_probability=0, source_quality=0, novelty=0, information_gain=0, weights={"mission_relevance": 0.9})
    assert tuned > base
