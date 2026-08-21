# BBStore Dashboard — Backend (Phase 2)

FastAPI backend that replaces the Phase 1 client-side/GitHub Pages setup for
the Orders → Report → Dashboard pipeline. See
`/Users/canhgiang/.claude/plans/temporal-rolling-crystal.md` for the full
approved plan (architecture, schema, endpoint list, phasing).

Master File / Combo / Dòng tiền / Điều chỉnh doanh thu are **not** part of
this migration — they stay client-side (IndexedDB) exactly as in Phase 1.

## Local development

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — the same FastAPI process serves both the API
(`/api/*`) and the static frontend (`frontend/`).

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

All business logic (column auto-detection, the Doanh số/GMV/status
derivation, DuckDB queries, JWT issue/verify) is covered without needing
real Supabase/R2 credentials — see `tests/conftest.py` for the dummy env
vars used.

## Environment variables

| Variable | Where to get it |
|---|---|
| `SUPABASE_URL` | Supabase project → Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase project → Settings → API → `service_role` key |
| `SUPABASE_JWT_SECRET` | Supabase project → Settings → API → JWT Secret |
| `R2_ACCOUNT_ID` | Cloudflare dashboard, right side of the R2 page |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | R2 → Manage API Tokens → the token you created |
| `R2_BUCKET_NAME` | The R2 bucket name (e.g. `bbstore-reports`) |
| `APP_JWT_SECRET` | Generate your own: `python -c "import secrets; print(secrets.token_urlsafe(48))"` — this signs the app's own login tokens, unrelated to Supabase's JWT secret |

Set all of these as environment variables directly in Render's dashboard —
never commit real values to the repo (`.env` is gitignored).

## Deploying (Render)

1. Render → New Web Service → select the `canhgiang96/bbstore` repo.
2. Root Directory: `backend`
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add the environment variables above.
6. Deploy. Health check: `GET /api/health` should return `{"ok": true}`.

(`render.yaml` in this folder documents the same config as a Blueprint, if
you'd rather deploy that way instead of the manual UI flow above.)

## Supabase setup (schema + accounts)

Run the schema SQL and the profile-seeding SQL that were provided earlier
in the project's SQL Editor — see the plan file for the exact statements.
Create one Supabase Auth user per person (Admin + viewers), then re-run the
`insert into profiles ...` statement so each has the right `role`.
