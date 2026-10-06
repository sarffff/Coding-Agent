def clamp(value: int, minimum: int, maximum: int) -> int:
    """Keep a value within the inclusive bounds."""
    # Demo defect: values below the minimum are not clamped yet.
    return min(value, maximum)
