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

## Current Blockers

| Area | Blocker | Safe unblock |
|---|---|---|
| OpenAI auto-issuance | `banto-sync-openai-admin` is not in Keychain | Store an OpenAI admin key with `banto store openai-admin`, then run the service-account rotator with the correct project id. |
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
entries should be written with Vercel's sensitive environment variable support,
which banto now uses for Vercel writes.
