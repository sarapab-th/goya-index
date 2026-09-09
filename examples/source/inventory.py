"""Synthetic demonstration only: not a real business rule or customer dataset."""


def calculate_stock_level(on_hand=50, reserved=8):
    """Return units available after reserving units for pending orders."""
    return on_hand - reserved
