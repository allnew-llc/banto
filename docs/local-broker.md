# 共通のKeychainアクセスプロセス

各AIアプリやスクリプトは操作を依頼し、常駐するbantoだけが秘密値を取得・使用します。
秘密値を返すAPIはありません。Pythonライブラリをimportするだけの従来経路とは異なります。

```text
Codex / Claude / ローカルMCPクライアント → stdioブリッジ ┐
Pythonスクリプト → BrokerClient                        ├→ Unix socket → banto常駐プロセス → Keychain
banto register → 本人用のブラウザ入力画面              ┘
```

## このMacでの起動

### PyPIからインストールした場合（推奨）

```sh
uv tool install "banto[mcp]"     # pipx install "banto[mcp]" でも可
banto broker install
banto broker health
banto broker enable
```

`install` は実行中のPython（uv tool / pipx のvenv）の絶対パスでLaunchAgentを登録します。
banto更新（`uv tool upgrade banto` など）の後は
`launchctl kickstart -k "gui/$(id -u)/work.allnew.banto.broker"` で再起動してください。
MCPクライアントの起動コマンドは `banto-mcp` で構いません（broker有効時は操作を転送します）。

### チェックアウトから起動する場合

リポジトリのMCP依存関係が入ったPythonを一度だけ指定します。実キーを読む必要はありません。

```sh
./.venv/bin/python -m banto.broker install
./.venv/bin/python -m banto.broker health
./.venv/bin/python -m banto.broker enable
```

LaunchAgent `work.allnew.banto.broker` がログイン時に起動します。実行Pythonと作業ディレクトリは
絶対パスで固定されます。`health` はPID・Pythonパスだけを返し、Keychainを読みません。
チェックアウトから起動した場合はそのソースを使用するため、移動・削除・Python環境更新前にはサービスを停止してください。
実行中プロセスへのコード変更は再起動後に反映されます。

既存のMCPクライアントは再起動が必要です。起動コマンドはリポジトリの
`run-banto-mcp.sh`（絶対パス）に統一します。このスクリプトは実行のたびにuvでPythonを選び直しません。
`banto-mcp` の既存エントリーポイントも、broker有効時には操作を転送します。
そのエントリーポイントの環境にはMCP依存関係が必要です。

## Codex・Claude Code・Gemini CLIの既定設定

```sh
python -m banto.broker_setup --workspace /path/to/workspace --launcher /absolute/path/run-banto-mcp.sh
python -m banto.broker_setup --workspace /path/to/workspace --launcher /absolute/path/run-banto-mcp.sh --apply
```

最初のコマンドは変更対象のパスだけ表示し、2番目で適用します。workspaceの `mcp/.mcp.json` と派生設定、
Codex・Claude Code・Gemini CLIのユーザー設定に同じbanto起動コマンドを登録します。
各クライアントのユーザー指示にも、秘密値を取り出さずbantoへ操作を依頼する規則を追加します。
`CLAUDE_CONFIG_DIR` が設定されている場合は、その有効なプロファイルの設定と指示にも反映します。
他のMCP、モデル、推論努力、認証、承認設定は維持します。自動承認・trust設定は追加しません。
既存のCodex banto設定が競合する場合は書き換えず停止します。適用後はクライアントの再起動が必要です。

ChatGPTのリモートコネクターはローカルUnix socket/stdioに直接接続できません。
既存のHTTP/SSE経路もbroker有効時に転送しますが、認証済みゲートウェイの設定は別途必要です。
HTTP/SSEは32文字以上のURL-safeな `BANTO_MCP_PATH_TOKEN` を必須とし、URLをログへ出しません。
この変更はトンネルを新規公開したり、ChatGPT/Codex/Claudeの接続設定を自動編集しません。

## PythonからAPIを利用する

```python
from banto.broker_client import BrokerClient

result = BrokerClient().call(
    "api_request",
    provider="openai",
    payload={"model": "<使用するモデル名>", "input": "入力", "max_output_tokens": 200},
)
```

対応する生成操作はOpenAI ResponsesとAnthropic Messagesの非ストリーミング呼び出しです。
モデルは呼び出し元が指定します。API利用料が発生するので、通常の予算・実行承認を維持してください。
キー名は既存のSyncConfigのサービスprefixと `openai` / `anthropic` を使用します。

### 予算管理が有効な場合（5.3.0以降）

既存の予算制限を迂回しません。hold/settleで次のように動作します。

1. payloadに `max_output_tokens`（OpenAI）/ `max_tokens`（Anthropic）が必須です。ない場合は
   `budget_requires_max_output_tokens` / `budget_requires_max_tokens` で拒否します。
2. モデルは `pricing.json` の `per_token` エントリが必要です。ない場合は `unpriced_model` で拒否します。
3. payloadのJSONバイト長を入力トークンの上限とみなし、最大出力と合わせた最大コストを永続holdとして
   予約します。グローバル・プロバイダ別・モデル別上限に収まらない場合は `budget_exceeded` です。
4. 成功時はレスポンスの `usage`（Anthropicはcache作成・読み取りトークンも入力に合算）で精算し、
   `banto_budget`（`settled_usd`・`reserved_usd`・`remaining_usd`）を付けて返します。
   usageがない場合は予約額のまま残します（`kept_usage_missing`）。
5. 4xxで拒否された場合はholdを解放します。ネットワークエラー・タイムアウト・5xxは課金の有無が
   不明なためholdを残し、`upstream_failed_or_outcome_unknown` を返します。残したholdは
   `hold_timeout_hours` では解除されず、その月の予算に計上されます。自動再試行はしません。

### レート制限と監査ログ

`api_request` は `config.json` の `broker.api_request_rpm`（1分あたり、既定30、`0`で無効）で制限され、
超過時は `rate_limited; retry after Ns` を返します。

すべての操作（`health` を除く）はruntimeディレクトリの `audit.jsonl`（0600）に1行ずつ追記されます。
記録するのは時刻（UTC）、連番、操作名、成否、エラーコード、許可リストのメタデータ
（provider、model、status、estimated/settled USD、hold id、トークン数、引数名）だけで、payload・
レスポンス本文・秘密値は記録しません。各レコードは直前レコードのSHA-256（`prev`）と自身のハッシュを持ち、
5MBで `.1` にローテーションしてもチェーンは続きます。

```sh
banto broker audit --tail 20     # 直近の記録
banto broker audit --verify      # チェーン検証（改ざん・欠落があれば終了コード1）
```

宛先・認証ヘッダー・プロキシは呼び出し元が指定できません。HTTPリダイレクトも拒否します。
OpenAIの保存は無効化します。リモートツール、ストリーミング等の未対応フィールドは拒否します。
参考: [OpenAI Responses](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)、
[Anthropic API](https://platform.claude.com/docs/en/api/overview)。

登録は `banto register openai` またはMCPの `banto_register_key` を使い、**このMacのブラウザ**で本人が入力します。
登録画面も常駐プロセスから起動し、そのプロセス内でKeychainへ保存します。

既存のsync・validate・lease・Secure EnclaveのMCP操作も共通プロセスへ転送します。
Secure Enclave署名の本人確認要件は変更しません。

## 保護する範囲

- socketは本人所有の0700ディレクトリ内に0600で作成し、接続元UIDも検証します。
- 秘密値取得、任意コード実行、任意URL、秘密値の直接登録のRPCは提供しません。
- 取得・保存した秘密値が操作の返答に含まれた場合は除去します。例外本文は返しません。
- サービス停止時は失敗し、直接Keychainを読む経路へフォールバックしません。
- タイムアウト後の変更操作は結果不明です。自動再試行せず、状態を確認してください。
- `enable` 後はbantoの従来の直接読み取り・書き込み・削除を拒否します。
  CLIの未移行のsync/lease操作も拒否します。既存スクリプトはBrokerClientへの移行が必要です。

これは**同一macOSユーザー内の共通サービス**です。アプリ署名ごとのアクセス制限や、悪意ある同一ユーザーの
Pythonコードを隔離するサンドボックスではありません。bantoを使わずSecurity.frameworkやsecurity CLIを
直接呼ぶ他リポジトリのコードまでは禁止できません。移行対象として個別に扱ってください。

「常に許可」の対象を固定するための構成であり、macOSの確認を無効化するものではありません。
初回・別Keychain項目・ロック解除・実行ファイル更新では本人の許可が必要な場合があります。
署名された専用ネイティブヘルパーはまだ実装していません。Pythonの更新をまたぐ恒久的な許可は保証しません。

## 停止・切り戻し

サービスだけ停止する場合は次を実行します。brokerモードが残るため直接読み取りには戻りません。

```sh
launchctl bootout "gui/$(id -u)/work.allnew.banto.broker"
```

意図的に従来モードへ戻す場合だけ `banto broker disable` を実行し、MCPクライアントを再起動します。
Keychain項目やACLは変更・削除しません。

## 検証

`tests/test_broker.py` と `tests/test_broker_budget.py` はダミー値のみで、socket通信、UID/パーミッション拒否、
例外・返答の秘密値除去、直接読み取り拒否、宛先制限、予算の予約・精算・保持、レート制限、監査ログの
ハッシュチェーン、MCP転送、本人入力画面へのルーティングを検証します。
本物のKeychain読み取り、外部API呼び出し、課金、認証操作は含みません。


### JSON input for `broker call`

Pipe exactly one UTF-8 JSON object and close stdin (EOF). For an operation
without arguments, send `{}` explicitly. `broker health` does not read stdin.
Interactive terminal input is rejected immediately. A pipe or redirected file
has five seconds to deliver its complete object and EOF, with a size limit of
`MAX_MESSAGE` bytes. Delayed or chunked input is accepted within that deadline;
empty input, non-object JSON, malformed/trailing data, oversized input, and a
producer that leaves the pipe open fail before a broker request is sent.
Input errors use the generic broker error message and do not echo the payload.

```sh
printf '%s' '{}' | banto broker call health
```
