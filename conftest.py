"""Shared pytest setup. Password hashing is slow on purpose; the tests use far fewer rounds so they run quickly
(each stored hash records its own round count, so this only affects hashes made during the tests)."""
import auth

auth._ITERATIONS = 1_000
