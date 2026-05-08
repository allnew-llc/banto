# Secret Rotation User Scenarios

この文書は、Vercelに登録しているAPI keyを、発行元サービスで再発行し、
発行された値をbanto経由でKeychainへ保存し、その後VercelへSensitive
Environment Variableとして反映するユーザーシナリオを定義する。

## Scope

- bantoはVercelの環境変数を「配布先」として扱う。
- Vercel Sensitive Environment Variablesは値が読み戻せないため、bantoは
  Vercelからsecret値を取得しない。
- bantoの信頼できる入力は、`sync.json`のメタデータ、Keychain内の現在値、
  発行元サービスが返す新しい値である。
- 新しい値はCLI出力、JSON出力、ログ、ドキュメントに表示しない。

## Actors

- Operator: 事故対応または定期ローテーションを実行する人。
- Issuer Service: xAI、OpenAI、Googleなど、API keyを発行するサービス。
- Local Vault: macOS Keychain。
- Deployment Target: Vercel project / environment。

## Scenario S1: xAI Voice Gateway Key Full-Auto Rotation

Preconditions:

- `sync.json`に`XAI_API_KEY`が登録され、Vercel targetが設定されている。
- xAI Management API keyが`XAI_MANAGEMENT_API_KEY`またはKeychain account
  `xai-management`に保存されている。
- xAI team idが分かっている。

Design note:

- xAIには既存key secretを即時rotateするAPIもあるが、旧secretをその場で
  無効化するため、Vercel反映失敗時のrollback余地が小さい。
- bantoでは事故対応の安全性を優先し、新規key作成、Vercel反映、smoke成功、
  旧key削除の順に進める。

Command:

```bash
banto sync xai-api-key xai --team-id <team_id> \
  --wait-propagation \
  --smoke-preset provider-validate \
  --revoke-api-key <old_api_key_id>
```

Expected flow:

1. bantoが`XAI_API_KEY`を`full_auto`として分類する。
2. xAI Management APIで新しいAPI keyを作成する。
3. 必要に応じてxAI propagation endpointでクラスタ反映を待つ。
4. 新しい値をKeychainへ保存する。
5. banto historyにfingerprintのみを記録する。
6. Vercelへ`vercel env add --sensitive --force --yes`で反映する。
7. smoke presetを実行する。
8. 成功後に旧xAI API keyを削除する。

Success criteria:

- 新しいkey値は一度も出力されない。
- Keychain保存、Vercel反映、smoke、旧key削除が成功する。
- 失敗時は新規作成keyを削除し、可能な場合は旧Keychain値へ戻す。

## Scenario S2: OpenAI Project Service Account Rotation

Command:

```bash
banto sync openai-service-account openai --project-id <proj_...> \
  --smoke-preset provider-validate \
  --revoke-service-account <old_service_account_id>
```

Expected flow:

1. OpenAI admin keyを環境変数またはKeychainから解決する。
2. project service accountを作成し、返却されたunredacted API keyを受け取る。
3. banto propagation flowでKeychainとVercelへ反映する。
4. 成功後に旧service accountを削除する。

## Scenario S3: Google API Key Rotation With Shared Gemini Account

Command:

```bash
banto sync google-api-key google-api-key --project-id <project> \
  --sync-shared-account-secrets \
  --smoke-preset provider-validate \
  --revoke-key <old_key_resource_name>
```

Expected flow:

1. Google OAuth access tokenを環境変数、ADC、gcloud authの順で解決する。
2. Google API Keys APIで新しいkeyを作成する。
3. primary secretをKeychainとVercelへ反映する。
4. 同じKeychain accountを共有するGemini secretも、明示opt-in時だけ同期する。
5. 成功後に旧Google keyを削除する。

## Scenario S4: Provider Without Full-Auto Adapter

Example:

```bash
banto sync propagate github --from-cli '<operator command>' \
  --smoke-preset provider-validate
```

Expected flow:

1. bantoはまず`full_auto` adapterの追加可否を確認する。
2. adapter未対応の場合、Operatorが発行元サービスの画面で新しい値を作成する。
3. Operatorはprovider別にカスタマイズされたbanto登録画面で、発行元URL、
   必要なKeychain account名、推奨env名、一括登録例を確認する。
4. 複数の関連keyがある場合はbatch modeでまとめてKeychainへ登録する。
5. Keychain保存、Vercel反映、smokeを共通flowで実行する。

Registration UX requirements:

- 登録画面はproviderごとに、発行元consoleへのリンク、推奨手順、
  自動rotatorがある場合のCLI例を表示する。
- xAIやOpenAIのようにruntime keyとmanagement/admin keyが分かれるproviderは、
  一括登録例を表示し、同じ画面で複数keyを保存できるようにする。
- provider-specific adapterが未実装でも、ユーザーは
  `provider|ENV_NAME=value` 形式で複数keyを貼り付け、一回の登録で
  Keychainへ保存できる。

## Scenario S5: Manual-Cutover Secrets Are Blocked

Example secrets:

- `ENCRYPTION_KEY`
- `HMAC_SECRET`
- `CRON_SECRET`
- webhook verification secrets

Expected flow:

1. bantoは`manual_cutover`として分類する。
2. `banto sync propagate`は実行前に拒否する。
3. Operatorは`docs/manual-cutover-rotation-runbook.md`に従う。

## Scenario S6: Failure Rollback And Cleanup

Failure examples:

- Vercel sync failure
- provider validation failure
- smoke failure
- shared-account propagation failure

Expected flow:

1. 新しいkey作成後に反映が失敗した場合、bantoは旧Keychain値へのrollbackを試す。
2. 新規作成した発行元keyを削除する。
3. cleanupやrollbackの結果を構造化して返す。
4. 旧keyの削除は成功後のみ実行する。

## Scenario S7: Vercel Sensitive Multi-Environment Push

Expected flow:

1. `Target.environments`が指定されていれば各environmentへ反映する。
2. Vercel driverは`--sensitive --force --yes`を付ける。
3. previewの全branch対象は空のgit branch引数で表現する。
4. Vercel以外のdriverには`environments`を強制しない。

## Scenario S8: Management Credential Missing

Expected flow:

1. 発行元管理keyが見つからない場合、bantoは発行前に停止する。
2. Keychain、Vercel、発行元サービスには副作用を起こさない。

## Completion Matrix

| Scenario | Required status |
|---|---|
| S1 xAI full-auto | Automated and tested |
| S2 OpenAI full-auto | Automated and tested |
| S3 Google full-auto | Automated and tested |
| S4 propagate-only | Automated and tested |
| S5 manual cutover block | Automated and tested |
| S6 rollback/cleanup | Automated and tested |
| S7 Vercel sensitive push | Automated and tested |
| S8 missing management credential | Automated and tested |
