from calculator import clamp


def test_clamp_below_minimum():
    assert clamp(-2, 0, 10) == 0


def test_clamp_above_maximum():
    assert clamp(12, 0, 10) == 10


def test_clamp_inside_range():
    assert clamp(5, 0, 10) == 5
