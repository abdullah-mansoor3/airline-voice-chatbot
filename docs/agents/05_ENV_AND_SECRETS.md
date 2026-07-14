# Environment Variables and Secrets

## Current state (audited)

| Variable | Status | Fix needed |
|---|---|---|
| `DUFFEL_API_KEY` | ✅ correct format (`duffel_test_...`) | Token must include `air.offer_requests.create` scope or flight search returns 403. Swap to `duffel_live_...` before production. |
| `SUPABASE_URL` | ✅ correct format | None |
| `SUPABASE_PUBLISHABLE_KEY` | ✅ correct, current format (`sb_publishable_...`) | This replaces the old `anon` key. Low-privilege, RLS-respecting, safe client-side. |
| `SUPABASE_SECRET_KEY` | ✅ correct format | This replaces the old `service_role` key — elevated, bypasses RLS, **server-side only**. Required for the Duffel webhook receiver and any write with no logged-in user session. |
| `SUPABASE_JWKS_URL` | ✅ present from Supabase connect prompt | Useful for future local JWT verification; current backend verifies sessions against Supabase Auth. |
| `SUPABASE_PASSWORD` | ⚠️ not currently present in local `.env` audit | Needed only to build/apply the direct Postgres connection string. |
| `SUPABASE_DIRECT_CONNECTION_STRING` | ❌ not present in local `.env` audit | Add this to apply `backend/db/schema.sql` from local tooling. |
| `PINECONE_API_KEY` | ✅ correct format (`pcsk_...`) | None |
| `PINECONE_INDEX_NAME` | ✅ present (`airline-policy-corpus`) | None |
| `GROQ_API_KEY` | ✅ correct format (`gsk_...`) | None |

## Full `.env.example` to commit (no real values)

```dotenv
# Duffel — live flight data
# Create the token at https://app.duffel.com/api-keys with scope:
#   air.offer_requests.create  (required for flight search)
#   air.orders.read            (optional, for live order status)
DUFFEL_API_KEY=

# Supabase
SUPABASE_URL=
SUPABASE_PUBLISHABLE_KEY=                  # sb_publishable_... — client-safe
SUPABASE_SECRET_KEY=                       # sb_secret_... — SERVER-SIDE ONLY, never ship this
SUPABASE_JWKS_URL=
SUPABASE_PASSWORD=
SUPABASE_DIRECT_CONNECTION_STRING=         # postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres

# Pinecone
PINECONE_API_KEY=
PINECONE_INDEX_NAME=airline-policy-corpus
PINECONE_CLOUD=aws
PINECONE_REGION=us-east-1

# Groq
GROQ_API_KEY=

# Frontend browser bundle — publishable values only
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/voice
```

## Rules for this project specifically

- `sb_publishable_...` and `sb_secret_...` go on the `apikey` header. Do not also send them on
  `Authorization: Bearer` — some client libraries do this by default, and since these are not
  JWTs, the request gets rejected. Pass the user's own session JWT there instead, or leave it
  empty for unauthenticated calls.
- `SUPABASE_SECRET_KEY` must never appear in frontend code, browser bundles, or any file inside
  `frontend/`. It belongs in `backend/` and the webhook receiver only.
- The browser uses only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`;
  WebSocket audio is rejected until the client sends a verified user access token.
- Local SQL application requires `SUPABASE_DIRECT_CONNECTION_STRING`; without it, the app can
  still use Supabase REST with `SUPABASE_SECRET_KEY`, but schema migrations cannot be applied
  from this repo.
- If the schema is applied manually through the Supabase SQL editor, re-run the latest
  `backend/db/schema.sql` after schema changes. As of 2026-07-09 the newest additions include
  `unique (user_id, title)` on `conversations` and the conversation delete RLS policy.
- Never paste real key values into a chat with an AI assistant, including this one — treat any
  key that's been pasted anywhere outside your own `.env` as compromised and rotate it.
- `.env` must be in `.gitignore` before the first commit, not after.
