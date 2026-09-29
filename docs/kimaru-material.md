# Kimaruの鍵・資格情報発行/格納（共通broker操作）

## 契約

`kimaru_material_capabilities`は固定の対応用途だけを返す。Keychainの項目や値を列挙しない。
`kimaru_material_provision`の引数は`environment`、`purpose`、`candidate_sha`、`approved_region`、`owner_confirm`だけ。secret値/URL/コマンド/driverを渡せない。Python呼出しは`BrokerClient`のみで、直接importして実行すると拒否する。

- 環境はstaging/production、subscriptionと各vaultは固定。japaneast/所有タグ/RBAC/purge/soft-deleteを確認する。ownerが事前承認した操作だけ実行する。
- 初期DB資格情報・Auth/quota/backup/rewrap/restore秘密はbantoサービス内で生成し、Keychainへ保存後、同じvaultの固定名へ格納する。owner/runtime/restore DSNは同じ環境の準備済み資格情報から内部で組み立てる。
- Stripe/Geminiは外部サービス側の本人発行が必要。`banto_register_key`へ`kimaru-staging-stripe-secret`等を指定し、このMacの本人用画面から登録する。秘密値をRPC/チャットへ渡さない。Gemini有料状態、Stripe本人確認/設定をこの操作で完了とはしない。
- RSA3072 KEKはbantoの指示でAzure内に生成・保持する。秘密鍵をKeychain/チャット/ファイルへ取り出さない。本番DEK生成・wrap/unwrapは既存のprivate Azure workloadで行い、Macの常時稼働へ依存させない。
- 戻り値は環境/用途/候補/状態/版付きURI、secretではSHA256 fingerprintのみ。値やprovider responseを返さない。

Azureの[Create Key](https://learn.microsoft.com/en-us/rest/api/keyvault/keys/create-key/create-key?view=rest-keyvault-keys-2025-07-01)は既存名へのPOSTで新版を作る。今回は既存を読み戻し、POSTしない。secretも[Set Secret](https://learn.microsoft.com/en-us/rest/api/keyvault/secrets/set-secret/set-secret?view=rest-keyvault-secrets-2025-07-01)の前に版metadataだけを確認し、既存値GETを行わない。複数版・disabled・不一致・soft-delete・cloudにだけある既存secretは停止。legacy値の自動移行やローテーションは未対応。

## 本人用の実行例（実行承認が必要）

Azure vaultが既に本人によって作成され、ローカル`az`へ本人ログイン済み、必要な権限が承認済みであることが前提。bantoは権限を追加しない。認証・Keychain許可は本人が操作する。

```python
from banto.broker_client import BrokerClient
broker = BrokerClient()
assert broker.call('kimaru_material_capabilities')['contract'] == 'kimaru-material-v1'
receipt = broker.call('kimaru_material_provision',
    environment='staging', purpose='db-key-rewrap-url',
    candidate_sha='<レビュー済みキマ〜ル40桁SHA>',
    approved_region='japaneast', owner_confirm=True)
# receiptは公開metadataだけ。戻り値以外の秘密値にアクセスしない。
```

bootstrap→derived DSNの順番で一つずつ実行する。未知の結果/timeoutでは自動再送せず、同じ用途のmetadataを確認する。再度本人が承認したprovisionは一致する1版だけを`already_verified`として返し、PUT/POSTしない。これはprovider間の原子的トランザクションではなく、Keychain保存だけが済む部分失敗を許容し、秘密の自動破棄/上書きを避ける。

## 検証と実受入

`tests/test_kimaru_material.py`はfake Keychain/ARM/Key Vaultと専用Unix socketを使用。実Keychain、Azure、課金、秘密発行はなし。既存broker regressionも実施する。実CLI token取得/HTTPS/権限/Keychain/Key Vaultの受入は別途必要。

既存の作業中checkoutを上書きせず、Git管理済み共通broker候補から別checkoutで開発した。mergeだけでは実行中のdaemonへ反映されない。稼働ソースの未commit操作を保護し、追加module/dispatcher差分だけを適用してidleを確認してからbrokerを再起動する。稼働中の未知のmutationを中断・再送しない。
