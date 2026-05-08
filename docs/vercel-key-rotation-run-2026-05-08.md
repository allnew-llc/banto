# Vercel Key Rotation Run — 2026-05-08

## Scope

Read-only inventory was run across the current `all-new` Vercel projects. Secret
values were not fetched or printed.

Command shape:

```bash
banto sync vercel-inventory \
  --project honntokoro-landing-page \
  --project moshimoshi-genki-xai-voice-gateway \
  --project allnew-corporate \
  --project allnew-corpo \
  --project web-app \
  --project airbnb \
  --project test_phase3_context_receives_n0 \
  --project allnew-apps \
  --project allnew-mobile-baas \
  --exclude-env XAI_API_KEY
```

## Inventory Summary

| Metric | Count |
|---|---:|
| Vercel env entries | 104 |
| Rotate now | 21 |
| Manual cutover | 34 |
| Monitor only | 47 |
| Review required | 0 |
| Excluded | 2 |
| Not yet managed by `sync.json` | 69 |
| Secret-like entries not marked `sensitive` | 46 |

## Excluded

`moshimoshi-genki-xai-voice-gateway` has two `XAI_API_KEY` entries
(`preview`, `production`). Both are marked `sensitive` and were created today,
so they are intentionally out of scope for this incident rotation run.

## Rotate Now

These need replacement credentials from the issuing provider, then banto
registration, Vercel propagation with `--sensitive`, redeploy, smoke test, and
old-key revocation.

| Project | Env names |
|---|---|
| `allnew-corporate` | `OPENAI_API_KEY`, `LINE_CHANNEL_ACCESS_TOKEN` |
| `honntokoro-landing-page` | `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_CHANNEL_SECRET`, `STRIPE_SECRET_KEY`, `TWILIO_AUTH_TOKEN`, `ZOOM_CLIENT_SECRET`, `EKYC_API_KEY`, `GBIZINFO_API_TOKEN`, `RESEND_API_KEY` |
| `allnew-mobile-baas` | `GOOGLE_AI_API_KEY` |

## Manual Cutover

These should not be blindly overwritten. They need staged runtime support or an
operator-approved window.

| Group | Env names |
|---|---|
| App secrets | `ENCRYPTION_KEY`, `HMAC_SECRET`, `CRON_SECRET`, `NEXTAUTH_SECRET`, `ADMIN_INVITE_CODE` |
| Database | `DATABASE_URL*`, `POSTGRES_URL*`, `POSTGRES_PRISMA_URL`, `POSTGRES_DATABASE_URL*`, `POSTGRES_PASSWORD`, `POSTGRES_PGPASSWORD`, `PGPASSWORD` |
| Webhooks | `STRIPE_WEBHOOK_SECRET`, `EKYC_WEBHOOK_SECRET`, `ZOOM_WEBHOOK_SECRET_TOKEN` |
| Upstash | `UPSTASH_REDIS_REST_TOKEN`, `UPSTASH_REDIS_REST_URL` |

## OpenAI Rotation Result

Status: completed for Vercel-managed `OPENAI_API_KEY` values in scope.

No secret values were printed or written to this document.

| Vercel project | OpenAI project | New credential | Vercel envs | Runtime verification |
|---|---|---|---|---|
| `allnew-corporate` | `Default project` (`proj_PpOBY9Y8sgxvhBoWTJfLHVAK`) | service account `user-AmkjIWnl6bIWXsYDm6rZqf0X`, API key id `key_X2mQk4G2ghKEe2XN` | `production` | redeployed to `https://allnew-corporate-bpt51pp25-all-new.vercel.app`; `https://allnew.work` returned HTTP 200; `/api/chat` returned HTTP 200 with the expected Origin header |
| `honntokoro-landing-page` | `hontonotoko.jp` (`proj_C1RDlpR9bThE895tysjkcAm0`) | service account `user-9MHjEfiNraK1Q5yN8kedjm83`, API key id `key_sh4sVLRP6ZwIcIR1` | `production`, `preview`, `development` | redeployed to `https://honntokoro-landing-page-pxq9ugftr-all-new.vercel.app`; `https://www.hontonotoko.jp` returned HTTP 200; `/api/chat/speaker` returned `OK` |

Follow-up cleanup:

- Revoked previous `hontonotoko.jp` service account `user-9sWaShTxIOThYI8aHFuSvuvn`.
- Deleted previous user-owned `hontonotoko.jp` Project API key `key_5cG6h4tkY4KaOo9O` after production smoke tests passed.
- Verified `hontonotoko.jp` now has one Project API key in scope: `key_sh4sVLRP6ZwIcIR1`, owned by service account `banto-honntokoro-20260508b`.
- Left `Default project` user-owned keys named `Claude_MCP_API_KEY` and `KabuPilot` untouched because their runtime ownership is not confirmed as Vercel-managed app credentials.

Implementation note:

- Vercel rejects Sensitive Environment Variables for the `development` target. Banto writes `production` and `preview` with `--sensitive`, and writes `development` without `--sensitive` while still using Vercel's encrypted environment variable storage.
- A Keychain account-resolution fix was required because agent shells can report `root` via `os.getlogin()` even when the user Keychain account is `masa`.

Local validation:

- `git diff --check`: passed.
- `uv run pytest`: 327 passed.

## Remaining Blockers

| Area | Blocker | Safe unblock |
|---|---|---|
| Google auto-issuance | Google ADC is not configured locally | Run `gcloud auth application-default login`, confirm the Google Cloud project id, then run the Google API key rotator. |
| Propagate-only providers | New provider keys are not available yet | Issue replacements in each provider dashboard, then use `banto sync propagate <name>` or the batch registration UI. |
| Manual cutover secrets | Runtime compatibility and rollback are not confirmed | Follow `docs/manual-cutover-rotation-runbook.md` before any overwrite. |

## Safe Execution Order

1. Rotate and validate provider API keys first: OpenAI, Google/Gemini, Resend,
   gBizINFO, eKYC, LINE, Stripe secret key, Twilio auth token, Zoom client
   secret.
2. Redeploy affected Vercel projects so new env vars are actually used.
3. Run app-level smoke tests against production/preview.
4. Revoke old provider credentials only after the smoke tests pass.
5. Schedule manual cutovers for DB, encryption, auth/session, cron, webhook, and
   Upstash secrets.

## Notes

Vercel environment variable changes only apply to new deployments. Sensitive
entries should be written with Vercel's sensitive environment variable support
where Vercel allows it, which banto now uses for `production` and `preview`
writes.
