"""Tests for the km ↔ miles conversion helpers."""

from __future__ import annotations

import pytest

from pipeline.units import KM_PER_MILE, MILES_PER_KM, km_to_miles, miles_to_km


def test_km_to_miles_known_value():
    # A 5 km run is ~3.1069 miles.
    assert km_to_miles(5.0) == pytest.approx(3.106855, rel=1e-6)


def test_miles_to_km_known_value():
    # A 3 mile run is ~4.828 km.
    assert miles_to_km(3.0) == pytest.approx(4.828032, rel=1e-6)


def test_round_trip_is_identity():
    assert miles_to_km(km_to_miles(12.34)) == pytest.approx(12.34, rel=1e-9)


def test_none_passes_through():
    assert km_to_miles(None) is None
    assert miles_to_km(None) is None


def test_constants_are_reciprocal():
    assert KM_PER_MILE * MILES_PER_KM == pytest.approx(1.0, rel=1e-12)
