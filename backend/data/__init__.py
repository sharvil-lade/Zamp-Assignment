"""Persistence: the database and document storage.

Every SQL statement in the project lives in `store`. Documents live in
`storage`, behind an interface that hides local disk vs Supabase.
"""
