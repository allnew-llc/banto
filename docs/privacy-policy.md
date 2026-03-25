# Privacy Policy - banto

**Effective Date**: March 25, 2026
**Last Updated**: March 25, 2026

## 1. Data Collection

banto is a local-first secret management tool. All API keys and secrets are stored exclusively in the macOS Keychain on your device.

- **No banto servers**: banto has no cloud backend, no hosted service, no servers that receive your data. **Exception**: the optional `banto chatgpt connect` feature routes metadata through third-party tunnel providers (ngrok or Cloudflare). See [Third-Party Data Sharing](#2-third-party-data-sharing) below.
- **No telemetry**: banto does not collect usage analytics, crash reports, or behavioral data.
- **No tracking**: banto does not use cookies, pixels, device fingerprinting, or any tracking mechanism.
- **No account required**: banto does not require registration or user accounts.

The only data banto stores is:
- **Keychain entries**: API keys stored in macOS Keychain (protected by Secure Enclave on supported hardware).
- **Configuration metadata**: Non-sensitive configuration files in `~/.config/banto/` containing target platform names, environment variable mappings, and sync state fingerprints. These files never contain secret values.

## 2. Third-Party Data Sharing

banto does not automatically share any data with third parties. The following user-initiated actions involve external communication:

### sync push (User-Initiated)
When you explicitly run `sync push` or use the `banto_sync_push` tool, banto sends secret values from your macOS Keychain directly to cloud platforms you have configured (e.g., Vercel, Cloudflare Workers, AWS, etc.). This is a deliberate deployment action. banto acts as a transport; it does not store, cache, or log the values in transit.

### validate (User-Initiated)
When you explicitly run `validate` or use the `banto_validate` / `banto_validate_keychain` tools, banto sends API keys to their respective provider endpoints (e.g., OpenAI, Anthropic, Google) to perform read-only health checks (typically GET requests to /v1/models or equivalent). No data beyond the API key header is sent.

### chatgpt connect (User-Initiated)
When you run `banto chatgpt connect`, banto starts a local MCP server and exposes it via a third-party tunnel service (ngrok or Cloudflare Tunnel). During this session, the tunnel provider has access to the following **metadata** (not secret values):
- Secret names (e.g., "openai", "github")
- Target platform names (e.g., "vercel", "cloudflare-pages")
- Sync status (push success/failure, drift detection results)
- Budget usage metrics (remaining balance, per-provider spend)
- Validation results (pass/fail/unknown per key)

The tunnel provider may also log connection metadata (IP addresses, timestamps, request sizes) per their own privacy policies. See [ngrok Privacy Policy](https://ngrok.com/privacy) and [Cloudflare Privacy Policy](https://www.cloudflare.com/privacypolicy/) for details on their data retention and sharing practices.

**Secret values are never included in MCP tool responses.** The tunnel URL contains a random capability token; only those with the URL can access the endpoint. The session ends when you stop the command (Ctrl+C).

Do not use `banto chatgpt connect` if you cannot accept that the metadata listed above may transit external tunnel providers.

All actions:
- Are triggered only by explicit user or agent commands, never automatically.
- Send data directly from your device to the target service (or via tunnel for ChatGPT), not through any banto server.

## 3. Data Storage

All data is stored on your macOS device:

| Data | Location | Protection |
|------|----------|------------|
| API keys and secrets | macOS Keychain | Secure Enclave / Keychain encryption |
| Sync configuration | `~/.config/banto/sync.json` | File system permissions (metadata only, no secret values) |
| Sync state and history | `~/.config/banto/` | File system permissions (fingerprint hashes only, no secret values) |
| Budget tracking | `~/.config/banto/` | File system permissions (cost data, no secrets) |

No data is stored on remote servers, cloud storage, or third-party services by banto itself.

## 4. Data Retention

- Secrets remain in macOS Keychain until you explicitly delete them via `banto delete`, `banto sync remove`, or macOS Keychain Access.
- Dynamic leases (short-lived credentials) expire automatically based on their configured TTL and are cleaned up via `banto lease cleanup`.
- Configuration files persist until manually deleted.
- There is no automatic data expiration for permanent secrets.

## 5. User Rights

You have full control over all data managed by banto:

- **Access**: View which secrets are stored via `banto sync status` (metadata only, values are never displayed).
- **Delete**: Remove any secret via `banto delete <name>` or `banto sync remove <name>`.
- **Export**: You can view your own secrets through macOS Keychain Access application.
- **Portability**: All configuration is in plain JSON files under `~/.config/banto/`.

No request to banto (the software or its maintainers) is needed to exercise these rights, as all data is local to your machine.

## 6. AI Agent Interaction

When banto is used as an MCP (Model Context Protocol) tool connected to AI agents (ChatGPT, Claude, etc.):

- The AI agent **never** receives secret values. All tool responses contain metadata only (status, fingerprints, counts).
- Key registration is performed via a local browser popup where the user enters the key directly. The agent never sees the value.
- Validation results return pass/fail/unknown status, not the keys themselves.

## 7. Children's Privacy

banto is a developer tool and is not designed for or directed at children under the age of 13. We do not knowingly collect personal information from children.

## 8. Changes to This Policy

Changes to this privacy policy will be published to the banto GitHub repository. Material changes will be noted in the CHANGELOG.

## 9. Contact

**AllNew LLC**
GitHub: [https://github.com/allnew-llc/banto/issues](https://github.com/allnew-llc/banto/issues)

---

# Privacy Policy - banto (Japanese / 日本語)

**発効日**: 2026年3月25日
**最終更新**: 2026年3月25日

## 1. データ収集

bantoはローカルファースト設計のシークレット管理ツールです。すべてのAPIキーおよびシークレットは、お使いのデバイス上のmacOSキーチェーンにのみ保存されます。

- **bantoサーバーなし**: bantoにはクラウドバックエンド、ホスティングサービス、データを受信するサーバーは一切存在しません。**例外**: オプションの `banto chatgpt connect` 機能は、第三者のトンネルプロバイダー（ngrok または Cloudflare）を経由してメタデータをルーティングします。詳細は下記「第三者へのデータ共有」をご覧ください。
- **テレメトリなし**: bantoは利用統計、クラッシュレポート、行動データを一切収集しません。
- **トラッキングなし**: bantoはCookie、トラッキングピクセル、デバイスフィンガープリント、その他のトラッキング手段を一切使用しません。
- **アカウント不要**: bantoにはユーザー登録やアカウントは必要ありません。

bantoが保存するデータ:
- **キーチェーンエントリ**: macOSキーチェーンに保存されるAPIキー（対応ハードウェアではSecure Enclaveにより保護）。
- **設定メタデータ**: `~/.config/banto/` 内の非機密設定ファイル。プラットフォーム名、環境変数マッピング、同期状態のフィンガープリントが含まれます。これらのファイルにシークレットの値は含まれません。

## 2. 第三者へのデータ共有

bantoは自動的に第三者とデータを共有することはありません。以下のユーザー起動アクションは外部との通信を伴います。

### sync push（ユーザー起動）
`sync push` コマンドまたは `banto_sync_push` ツールを明示的に実行すると、bantoはmacOSキーチェーンから設定済みのクラウドプラットフォーム（Vercel、Cloudflare Workers、AWSなど）へシークレット値を直接送信します。これは意図的なデプロイ操作です。bantoは転送手段として機能し、転送中の値を保存、キャッシュ、またはログに記録しません。

### validate（ユーザー起動）
`validate` コマンドまたは `banto_validate` / `banto_validate_keychain` ツールを明示的に実行すると、bantoは各APIキーをそれぞれのプロバイダーエンドポイント（OpenAI、Anthropic、Googleなど）に送信し、読み取り専用のヘルスチェック（通常は /v1/models 等へのGETリクエスト）を実行します。APIキーヘッダー以外のデータは送信されません。

### chatgpt connect（ユーザー起動）
`banto chatgpt connect` を実行すると、bantoはローカルMCPサーバーを起動し、第三者のトンネルサービス（ngrokまたはCloudflare Tunnel）を経由して公開します。このセッション中、トンネルプロバイダーは以下の**メタデータ**（シークレット値ではありません）にアクセスできます:
- シークレット名（例: "openai", "github"）
- ターゲットプラットフォーム名（例: "vercel", "cloudflare-pages"）
- 同期状態（プッシュの成功/失敗、ドリフト検出結果）
- 予算使用量メトリクス（残額、プロバイダ別支出）
- 検証結果（キーごとの pass/fail/unknown）

トンネルプロバイダーは接続メタデータ（IPアドレス、タイムスタンプ、リクエストサイズ）をログに記録する場合があります。詳細は [ngrok プライバシーポリシー](https://ngrok.com/privacy) および [Cloudflare プライバシーポリシー](https://www.cloudflare.com/privacypolicy/) をご確認ください。

**シークレット値はMCPツールレスポンスに含まれません。** トンネルURLにはランダムなケイパビリティトークンが含まれ、URLを知る者のみがアクセスできます。セッションはコマンドの停止（Ctrl+C）時に終了します。

上記のメタデータが外部トンネルプロバイダーを通過することを受け入れられない場合は、`banto chatgpt connect` を使用しないでください。

いずれの操作も:
- ユーザーまたはエージェントの明示的なコマンドによってのみ実行され、自動的には行われません。
- データはお使いのデバイスから対象サービスへ直接送信されます（ChatGPT連携時はトンネル経由）。bantoサーバーを経由することはありません。

## 3. データ保存

すべてのデータはお使いのmacOSデバイスに保存されます。

| データ | 保存場所 | 保護方式 |
|--------|----------|----------|
| APIキー・シークレット | macOSキーチェーン | Secure Enclave / キーチェーン暗号化 |
| 同期設定 | `~/.config/banto/sync.json` | ファイルシステム権限（メタデータのみ、シークレット値なし） |
| 同期状態・履歴 | `~/.config/banto/` | ファイルシステム権限（フィンガープリントハッシュのみ、シークレット値なし） |
| バジェットトラッキング | `~/.config/banto/` | ファイルシステム権限（コストデータ、シークレットなし） |

banto自体がリモートサーバー、クラウドストレージ、または第三者サービスにデータを保存することはありません。

## 4. データ保持

- シークレットは `banto delete`、`banto sync remove`、またはmacOSキーチェーンアクセスから明示的に削除するまでキーチェーンに保持されます。
- 動的リース（短期間の認証情報）は設定されたTTLに基づいて自動的に期限切れとなり、`banto lease cleanup` でクリーンアップされます。
- 設定ファイルは手動で削除するまで保持されます。
- 永続シークレットの自動的なデータ失効はありません。

## 5. ユーザーの権利

bantoが管理するすべてのデータに対して完全な制御権があります。

- **アクセス**: `banto sync status` でどのシークレットが保存されているかを確認できます（メタデータのみ、値は表示されません）。
- **削除**: `banto delete <name>` または `banto sync remove <name>` で任意のシークレットを削除できます。
- **エクスポート**: macOSキーチェーンアクセスアプリケーションを通じてシークレットの値を確認できます。
- **ポータビリティ**: すべての設定は `~/.config/banto/` 配下のプレーンJSONファイルです。

すべてのデータはお使いのマシンにローカルに保存されているため、これらの権利の行使にbanto（ソフトウェアまたはその管理者）への問い合わせは不要です。

## 6. AIエージェントとの連携

bantoがMCP（Model Context Protocol）ツールとしてAIエージェント（ChatGPT、Claudeなど）に接続される場合:

- AIエージェントはシークレットの値を**受け取ることはありません**。すべてのツールレスポンスにはメタデータのみ（ステータス、フィンガープリント、件数）が含まれます。
- キーの登録はローカルブラウザのポップアップを通じて行われ、ユーザーが直接キーを入力します。エージェントは値を参照しません。
- バリデーション結果はpass/fail/unknownのステータスのみを返し、キー自体は返しません。

## 7. 児童のプライバシー

bantoは開発者向けツールであり、13歳未満の児童を対象としたものではありません。児童から個人情報を故意に収集することはありません。

## 8. ポリシーの変更

本プライバシーポリシーの変更はbantoのGitHubリポジトリに公開されます。重要な変更はCHANGELOGに記載されます。

## 9. お問い合わせ

**AllNew LLC**
GitHub: [https://github.com/allnew-llc/banto/issues](https://github.com/allnew-llc/banto/issues)
