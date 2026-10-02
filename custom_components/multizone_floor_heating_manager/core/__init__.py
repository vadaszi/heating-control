"""Pure-Python control core of Multizone Floor Heating Manager.

Rules:
- no Home Assistant imports;
- the core never reads the system clock; time is always passed in.

Both rules are enforced by tests/core/test_core_purity.py.
"""
