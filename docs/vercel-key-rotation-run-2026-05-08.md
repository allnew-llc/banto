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

### OpenAI Organization Migration

Status: Vercel-managed `OPENAI_API_KEY` values were migrated from the
individual-owned OpenAI organization to the `platform-admin@allnew.work`
organization.

No secret values were printed or written to this document.

| Vercel project | New OpenAI project | New credential | Vercel envs | Runtime verification |
|---|---|---|---|---|
| `allnew-corporate` | `allnew-corporate` (`proj_6Yn80srRYmWL9S6yF2m0Apzw`) | service account `user-WPrTpXI38hp5vsuoEaJQGvBC`, API key id `key_vcta9hJeR7GrQ9qo` | `production` | redeployed to `https://allnew-corporate-4653gcyh4-all-new.vercel.app`; `https://allnew.work` returned HTTP 200; `/api/chat` returned HTTP 200 |
| `honntokoro-landing-page` | `hontonotoko.jp` (`proj_Qre6UYE9e21gr289bJjo9HEP`) | service account `user-mkw0NLN8Z8T13kGVt21qBrm6`, API key id `key_TakuIOkrgCBBnwhZ` | `production`, `preview`, `development` | redeployed to `https://honntokoro-landing-page-cjhnm8vjs-all-new.vercel.app`; `https://www.hontonotoko.jp` returned HTTP 200; `/api/chat/speaker` returned HTTP 200 after billing credits were added |

Migration notes:

- Banto `openai-admin` now resolves to a `platform-admin@allnew.work` OpenAI
  Admin Key from `keychain:banto-sync:openai-admin`.
- The first production smoke after migration returned OpenAI `429` quota errors
  for `honntokoro-landing-page`; adding credits to the new OpenAI organization
  cleared the error without another key change.
- Do not revoke old individual-owned OpenAI organization credentials until the
  old organization can be accessed intentionally and the target service accounts
  are confirmed there.

## Google/Gemini Rotation Result

Status: completed for Vercel-managed Google/Gemini API keys in scope.

No secret values were printed or written to this document.

| Vercel project | Google Cloud project | New credential | Vercel envs | Runtime verification |
|---|---|---|---|---|
| `honntokoro-landing-page` | `gen-lang-client-0469915824` | API key `projects/402783811468/locations/global/keys/58c27495-6d64-4977-96f0-7bf767e6f36d` (`banto-google-api-key-20260508t134545z`) | `GOOGLE_API_KEY` production, `GEMINI_API_KEY` production | redeployed to `https://honntokoro-landing-page-1buvwlxf6-all-new.vercel.app`; `https://www.hontonotoko.jp` returned HTTP 200 |
| `allnew-mobile-baas` | `gen-lang-client-0469915824` | same API key as above | `GOOGLE_AI_API_KEY` production, preview, development | redeployed to `https://allnew-mobile-baas-qcgnszq5p-all-new.vercel.app`; alias `https://allnew-mobile-baas.vercel.app`; `/api/health` returned HTTP 200; `/api/gemini/live-token` returned HTTP 405 to unauthenticated GET, confirming the route is deployed and POST-only |

Follow-up cleanup:

- Deleted previous Banto-managed Google API key `projects/402783811468/locations/global/keys/16739556-885c-4a50-89ad-7d26ba2068cf`.
- Verified the Google Cloud project now has one Banto-managed API key, plus the separate `Xcode API key`, which was left untouched because it is not a Vercel-managed app credential.
- Added local Banto sync config entry `google-ai-api-key` for `allnew-mobile-baas` so `GOOGLE_AI_API_KEY` is now managed by the same Keychain account as `GOOGLE_API_KEY` and `GEMINI_API_KEY`.
- Vercel metadata read-back showed `production`/`preview` entries as `sensitive`; `development` remains `encrypted` because Vercel does not allow `--sensitive` for development variables.

## Remaining Blockers

| Area | Blocker | Safe unblock |
|---|---|---|
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
