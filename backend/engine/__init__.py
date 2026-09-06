"""The deterministic decision engine.

`rules` holds the 17 validation rules and `decide()`; `pipeline`
sequences the stages and owns the audit events; `forms` turns a form
schema into something the rules can read. This is the only place a
status is produced.
"""
