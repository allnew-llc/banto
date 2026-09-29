# Kimaru R0：予算付きの合成評価

## 契約

`kimaru_evaluation_capabilities`、本人承認後の`kimaru_evaluation_prepare`、`kimaru_evaluation_generate`を共通brokerへ追加。`BrokerClient(timeout=360)`で呼ぶ。一般の`api_request`へfallbackしない。callerのkey/endpoint/driver/budget値、tool、media、会話履歴、streaming、provider変更はgenerateに渡せない。

R0専用の合成3社/A・B計6ケース、同じKimaru候補SHA、最大40呼出し/180k出力token/入力＋出力800k tokenをケースごとに維持。`syntheticOnly`はこの評価の用途と本人確認の宣言であり、任意文字列が合成データであることを機械的に証明する機能ではない。実行側は固定の検査済み合成fixtureと確認済み公的資料だけを使う。顧客文書には使わない。

- Azureは固定subscription/resource/deployment。ARM metadataからeastus、gpt-6-sol/2026-09-22、GlobalStandardとendpointを照合してEntra tokenで送る。これは合成検証の許可で、本番のD7/D8・国内処理承認ではない。[公式Responses仕様](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)。
- Geminiは既存sync登録`gemini-api-key`の固定account `claude-mcp-gemini`だけをservice内で読む。read-only preflightでGoogle lookupKeyの所属番号402783811468、対象project `gen-lang-client-0469915824`のbillingEnabled、固定モデルのGETを照合。Google OAuthは登録済みCLIセッションの固定accountからservice内だけで取得。Paid Tierと前払い残高はAPIのbillingEnabledとは別にAI Studioで確認する。キー値・lookup URL・OAuth tokenを返さない。[公式generateContent/usage仕様](https://ai.google.dev/api/generate-content)。
- metadata/credentialsを準備する前に月額予算とbatch残額を検査。送信前に共通CostGuardのdurable holdと評価ledgerをfsync保存する。キー/本文/生のAPIレスポンスは予算ログへ保存しない。
- 完了usageではGeminiの思考tokenも出力に合算、cache値も記録。入力4/出力15 USD/100万tokenを両providerの保守的な予約・消費上限計算に使う。割引/cache控除なし。**実料金表の見積りや実請求額ではない。** 同じusage×確認済み実単価の価格検討は別に行う。
- timeout、HTTP error、usage欠落/不正/上限超過、部分保存、未settleの予約は解除・再送しない。評価全体を停止。request_idの再利用は送信前に拒否。時刻/月/プロセスが変わっても評価ledgerの予約は残り、共通durable holdもtimeout対象外。
- strict CostGuardは不正JSON/負の値/NaN/unsafe permission/symlinkを失敗として扱う。旧一般CostGuardの回復/期限解除挙動は既定で維持し、durable entryだけ期限解除しない。評価に使う既存月額usage fileは本人管理のmode600が必要。自動chmod/予算増額をしない。

## 初回準備（本人の明示予算承認・Paid Tier確認後だけ）

現在の50 USD提案への回答をこの文書で代行しない。PR #12時点では予算変更、policy作成、実API、Keychain、Azureへの送信を行っていない。2026-09-29オーナーが50 USDを承認。実運用側はPaid Tier照合と同候補policy準備後に評価する。月額予算は既存bantoの本人用経路で設定し、当該batchを十分収容できる残額が必要。prepareは月額予算を変えない。

```python
from datetime import datetime, timedelta, timezone
from banto.broker_client import BrokerClient
b = BrokerClient(timeout=360)
receipt = b.call('kimaru_evaluation_prepare',
    candidate_sha='<承認済み40桁Kimaru SHA>', limit_usd='50.00',
    expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
    owner_confirm=True, gemini_paid_tier_confirmed=True)
# metadataだけ。policyと空のledgerは固定 ~/.config/banto にmode600で作成。
```

存在するpolicy/ledgerを自動上書きせず、部分作成も停止する。期限は最大7日、batch上限50 USD。候補変更/追加評価/unknown結果の解決は本人がusageとledgerを確認し、別の具体的な変更計画を承認する。削除して予算を再利用する手順は提供しない。二つの予算ledgerと外部課金は原子的な一取引ではなく、途中で止まると予算が保守的に残る。

## 検証と受入

fake provider、private temporary policy/ledger、Unix socketで、ゼロ予算・Paid Tier未確認・候補違い・壊れたledger・重複・case/batch上限・unknown・月変わり・保存失敗を確認する。実資格情報、実token/HTTPS/CLI/モデル/Paid Tierの受入は未実施。runtimeへの配置・capabilities確認と、実生成品質の完了を区別する。

## 既存キーのread-only preflight

`BrokerClient().call('kimaru_evaluation_preflight')`はservice-only。任意引数・キー値の返却・別account/projectへのfallbackはない。固定登録/所属プロジェクト/billingEnabled/modelのいずれかが不明・不一致なら停止。本文・生成・課金POSTは0件。API keys lookupはGoogle公式の所属確認GETを使う。通常のproviderエラー本文やURLを出力しない。[公式仕様](https://docs.cloud.google.com/api-keys/docs/get-info-api-keys)。

既存DiagnosisGeneratorの上限12288に合わせ、1送信の出力最大を12288へ修正。case/batch予算・180k出力/800k合計/40callの上限は維持。preflightと上限境界のfake検証8件を追加、従来回帰を合わせ89件成功。
