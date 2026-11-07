# Database

Default is SQLite (`SIMS_DATABASE_URL=sqlite:///./sims.db`). For PostgreSQL:

```
pip install "psycopg[binary]>=3.2"
SIMS_DATABASE_URL=postgresql+psycopg://sims:<password>@localhost:5432/sims
```

Tables: `users`, `sites`, `access_points`, `clients`, `ap_samples`, `site_samples` (history), `alerts`,
`audit_log`, `config_backups` (redacted running-config snapshots), `config_changes` (plans/applies; secrets Fernet-encrypted), `client_events` (inferred connect/disconnect/roam), `app_settings` (e.g. scoring configuration), `alembic_version`.

**Migrations (Alembic).** The app applies migrations automatically at every start (`create_schema()` runs
`alembic upgrade head`); you never run anything by hand.

- `0001` is the baseline: the tables that existed before migrations (Phase 1 + configuration management).
- `0002` adds Phase 2 (experience): `client_events`, `app_settings`, and the experience columns.
- A database created by an earlier SIMS version (no `alembic_version` table) is recognized, any missing baseline tables
  are added, it is stamped `0001`, and only newer migrations run. Your sites, APs and history are kept.
- If a migration fails the app stops with a clear message and leaves the data as it was. **Back up `sims.db` before
  upgrading** (copy the file while SIMS is stopped).
- Developers: after changing `backend/database/models.py`, run
  `alembic revision --autogenerate -m "describe change"` and review the generated file. The `schema_version`
  table is a leftover from before Alembic and is no longer used.

Retention: `SIMS_RETENTION_DAYS` (default 30) controls how long samples are kept.
