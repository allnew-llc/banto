# Manual-Cutover Rotation Runbook

This runbook covers sync-managed secrets that are intentionally classified as
`manual_cutover` because a blind overwrite can break production behavior.

## When to use this runbook

Use this workflow for secrets such as:

- `ENCRYPTION_KEY`
- `HMAC_SECRET`
- `CRON_SECRET`
- webhook verification secrets
- database credentials that are tightly coupled to application rollout

Do **not** use `banto sync propagate` as the first move for these secrets.

## Safety Principles

1. separate issuance from cutover
2. prefer dual-read or overlap windows over big-bang replacement
3. validate in preview or a non-critical environment first
4. keep rollback material until production health is confirmed
5. revoke the old credential only after runtime behavior is stable

## Pre-Flight Checklist

- confirm the affected systems and owners
- identify whether the secret is used for read, write, verify, encrypt, or sign
- verify whether multiple active values are supported
- prepare rollback materials
- identify the smoke test and health signals
- confirm where the value is stored outside Vercel, if anywhere

## Pattern A: Encryption Keys

Use for `ENCRYPTION_KEY` and similar application-level encryption keys.

1. implement dual-read, new-write behavior
2. deploy code that can decrypt with both old and new keys
3. issue the new key and store it safely
4. update the runtime to write new ciphertext with the new key
5. monitor decryption errors and background jobs
6. backfill old ciphertext if required
7. remove the old key only after the overlap window is complete

Rollback:

- revert to old-write behavior if decryption or data migration fails
- keep both keys loaded until read success recovers

## Pattern B: Signing and Verification Secrets

Use for `HMAC_SECRET`, `CRON_SECRET`, and webhook verification secrets.

1. confirm whether verifiers can accept multiple active secrets
2. if supported, add the new secret as an additional valid verifier
3. update producers to start signing with the new secret
4. verify requests signed with the new secret are accepted
5. monitor signature failures, webhook retries, and job scheduling
6. remove the old secret after the overlap window

Rollback:

- restore producer signing to the old secret
- keep dual verification enabled until the source of failures is resolved

## Pattern C: Database Credentials

Use when credential rotation changes connectivity or privilege boundaries.

1. create a new database principal or password
2. grant the minimum required privileges
3. test the new credential in a non-critical environment first
4. deploy the new credential to production
5. confirm connection pool health, migrations, and write paths
6. remove or disable the old credential only after stable health

Rollback:

- revert the application to the old credential
- keep the new credential available until rollback is complete

## Recommended banto Usage

For `manual_cutover` secrets, use `banto` only after the staged plan is ready.

- use `banto sync incident-report` to confirm the secret is in the `Manual Cutover` lane
- use `banto sync classify` to confirm the current rotation class
- update Vercel only at the exact cutover point in the runbook

## Exit Criteria

- smoke tests pass
- runtime errors remain normal
- old credential is retired
- rollback materials are archived or intentionally removed
