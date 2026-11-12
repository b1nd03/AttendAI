import pytest
from scripts.evaluate_thresholds import evaluate,wilson_upper

def test_no_false_accepts_does_not_mean_zero_risk():
    rows=[{'label':'genuine','score':.8}]*10+[{'label':'impostor','score':.1}]*10
    result=evaluate(rows,.001)
    assert result['recommended_threshold'] is None
    assert wilson_upper(0,10)>.2

def test_enough_separated_scores_can_support_an_operating_candidate():
    rows=[{'label':'genuine','score':.8}]*100+[{'label':'impostor','score':.2}]*5000
    assert evaluate(rows,.001)['recommended_threshold']==.4

def test_invalid_or_missing_scores_are_rejected():
    with pytest.raises(ValueError):evaluate([{'label':'genuine','score':.8}],.01)
    with pytest.raises(ValueError):evaluate([{'label':'genuine','score':float('nan')},{'label':'impostor','score':.2}],.01)
