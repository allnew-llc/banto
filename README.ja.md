[English](./README.md)

# banto

LLMエージェントの想定外の稼働による過剰課金を構造的に抑制するための、予算制御付きAPIキー金庫です。APIキーは `.env` や環境変数ではなく安全なバックエンド（デフォルト: macOS Keychain、1Password等に差し替え可能）に格納し、予算の範囲内でのみ返却します。

> **番頭**（bantō）── 江戸時代の商家において蔵の鍵と帳簿を預かり、主人の留守にも商いの秩序を守った筆頭番頭に由来します。

## 課題

多くのプロジェクトでは、APIキーを `.env` ファイルや環境変数に保管しています。この場合、どのプロセスからでもキーを読み取れるため、エージェントが予算チェックの例外を無視してAPIを呼び続ける可能性があります。エージェントが想定外に稼働した結果、高額な請求が発生するリスクがあります。

## 解決策

bantoは予算制御を**構造的**に実現します。APIキーはmacOS Keychainに格納され、bantoのAPIを通じて予算の範囲内でのみ返却されます。予算超過時はキーそのものが取得不能になるため、APIコールが成立しません。

```
エージェントがAPIキーを要求
        |
        v
  [予算ホールド] ──超過──> BudgetExceededError（キーは返されない）
        |
       OK（見積もりコストを予約）
        v
  [Keychain参照] ──> APIキーを返却
        |
        v
  エージェントがAPIを呼ぶ
        |
        v
  [ホールド精算] ──> 実コストで精算、余剰予算を解放
```

## 動作要件

- macOS（Keychainによるシークレット管理）
- Python 3.10+
- 外部依存なし

## インストール

```bash
pip install banto
```

ソースからインストールする場合:

```bash
git clone https://github.com/allnew-llc/banto.git
cd banto
pip install -e .
```

## クイックスタート

### 1. 設定の初期化

```bash
banto init    # デフォルト設定を ~/.config/banto/ に配置
```

### 2. 月次予算の設定

デフォルトの予算上限は **$0（米ドル）** です。ご自身で予算を設定してください。予算が $0 のままだと、すべてのAPIキー取得がブロックされます。

```bash
banto budget 50    # グローバル月次上限を $50 USD に設定
```

すべての予算は**米ドル（USD）**建てで、**暦月**単位で管理されます。毎月1日に自動リセットされます。

### 3. APIキーの登録

使用するプロバイダごとに `banto store <provider>` を実行し、APIキーをmacOS Keychainに登録します。キーの入力はマスクされ、画面に表示されません。

```
$ banto store openai
Enter API key for 'openai':    ← ここにキーを貼り付けます（入力は非表示）
Stored 'openai' in Keychain.
```

すでにキーが登録されている場合は、上書きするかどうか確認されます。

```
$ banto store openai
Key for 'openai' already exists. Overwrite? (y/N): y
Enter API key for 'openai':
Stored 'openai' in Keychain.
```

APIキーは各プロバイダのダッシュボードから取得できます。

- **OpenAI**: https://platform.openai.com/api-keys
- **Google**: https://aistudio.google.com/apikey
- **Anthropic**: https://console.anthropic.com/settings/keys

使用するプロバイダごとに繰り返します。

```bash
banto store openai
banto store google
banto store anthropic
```

### 4. コードへの組み込み

```python
from banto import SecureVault, BudgetExceededError, KeyNotFoundError

vault = SecureVault(caller="my_app")

try:
    # 予算ホールド + キー取得（見積もりコストを予約してからキーを返す）
    key = vault.get_key(
        model="gpt-4o",
        input_tokens=1000,
        output_tokens=500,
    )

    # キーを使用してAPIを呼ぶ
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[...],
        api_key=key,
    )

    # 実コストで精算（余剰予算が解放される）
    vault.record_usage(
        model="gpt-4o",
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        provider="openai",
        operation="chat",
    )

except BudgetExceededError as e:
    print(f"予算超過: 残り${e.remaining:.2f} / 上限${e.limit:.2f}")

except KeyNotFoundError as e:
    print(f"キー未登録: banto store {e.provider}")
```

## CLI

```bash
banto status              # 予算状況の表示（プロバイダ別・モデル別の内訳付き）
banto budget [args]       # 予算上限の表示・設定
banto store <provider>    # APIキーをKeychainに登録
banto delete <provider>   # APIキーをKeychainから削除
banto list                # 登録済みキーと予算の一覧
banto check <model> ...   # コスト見積もり（ドライラン）
banto init                # ユーザー設定の初期化
```

### 予算管理

```bash
# 全上限の表示
banto budget

# グローバル月次上限の設定
banto budget 100

# プロバイダ別上限の設定
banto budget --provider openai 30

# モデル別上限の設定
banto budget --model dall-e-3 10

# 上限の削除
banto budget --provider openai --remove
```

### コスト見積もりの例

```bash
# トークン課金モデル
banto check gpt-4o --tokens 1000 500

# 画像生成
banto check dall-e-3 --n 4 --quality hd --size 1024x1024

# 動画生成
banto check sora-2 --seconds 10
```

## 設定

デフォルト設定は `~/.config/banto/` に配置されます。`banto init` で `config.json`（予算設定）と `pricing.json`（料金テーブル）の2ファイルが作成されます。

### 予算上限

すべての予算は**米ドル（USD）**建てで、**暦月**単位で管理されます。デフォルトは `0` です。ご自身で予算上限を設定してください。

```json
{
  "monthly_limit_usd": 0
}
```

### プロバイダ・モデル対応表

モデル名からプロバイダを自動解決し、適切なKeychainエントリを参照します:

```json
{
  "providers": {
    "openai": {
      "models": ["gpt-4o", "dall-e-3", "sora-2"]
    },
    "google": {
      "models": ["gemini-3-pro-image-preview", "imagen-4.0-generate-001"]
    }
  }
}
```

### 料金設定（`pricing.json`）

料金テーブルは予算設定とは**別ファイル**（`~/.config/banto/pricing.json`）で管理されます。2026年3月時点のOpenAI・Anthropic・Googleの主要モデルの料金がサンプルとして同梱されています。

> **料金は静的であり、正確性は保証されません。** bantoはプロバイダのAPIから実行時に料金を取得しません。主要プロバイダ（OpenAI、Anthropic、xAI）はモデル単価を返す公開APIを提供していないためです。各プロバイダの公式料金ページで最新の料金を確認し、`pricing.json` を更新してください。料金テーブルの不正確さに起因する損害について、AllNew LLCは一切の責任を負いません。

プロバイダが料金を改定した場合は、`~/.config/banto/pricing.json` を編集してください。新しいモデルを追加する場合は、`config.json` の `providers`（キー解決用）と `pricing.json`（コスト算定用）の両方にエントリを追加します。

3種類の課金体系に対応しています:

```json
{
  "gpt-4o": {
    "type": "per_token",
    "input_per_1k": 0.0025,
    "output_per_1k": 0.01
  },
  "dall-e-3": {
    "type": "per_image",
    "variants": {
      "standard_1024x1024": 0.040,
      "hd_1024x1024": 0.080
    },
    "fallback": 0.120
  },
  "sora-2": {
    "type": "per_second",
    "rate": 0.10
  }
}
```

## 仕組み

### ホールド/精算パターン（Hold/Settle）

bantoは**悲観的予約**（ペシミスティックリザベーション）パターンで予算を管理します:

1. **ホールド**: `get_key()` は見積もりコストを使用量ログに予約エントリとして書き込んでからキーを返します。予約額は即座に予算から差し引かれます。
2. **精算**: `record_usage()` は対応するホールドを検索し、実コストで置き換えます。見積もり > 実コストの場合、差額の予算が解放されます。
3. **安全側バイアス**: `record_usage()` が呼ばれなかった場合（クラッシュ、タイムアウト等）、ホールド分はそのまま残ります。予算が暗黙的に漏れることはありません。

このパターンにより、`get_key()` でキーを取得した後にコスト記録をスキップする、というメータリングギャップが解消されます。

### 多段階予算制御

`get_key()` 呼び出し時に3層のチェックを行います（すべて通過する必要があります）:

1. **グローバル上限**: 全プロバイダ・全モデル合算の月次上限
2. **プロバイダ上限**: プロバイダ単位のキャップ（例: OpenAI $30/月）
3. **モデル上限**: モデル単位のキャップ（例: DALL-E 3 $10/月）

### 使用量追跡

- 使用量は `~/.config/banto/data/usage_YYYY_MM.json` にコールごとに記録されます
- 月が変わると新しいファイルが作成され、予算が自動リセットされます
- 合計値はファイル読み込みのたびにエントリから再計算されるため、値のドリフトが発生しません
- `fcntl` によるファイルロックでプロセス間の排他制御を行います

### Keychainストレージ

- キーはログインKeychainにジェネリックパスワードとして格納されます
- サービス名の形式: `banto-<provider>`（例: `banto-openai`）
- macOS `security` CLIを使用（ネイティブバインディング不要）
- キーはディスクに書き出されません（`.env` ファイルや設定ファイルは不要）
- 注意: `banto store` 実行時、キーは `security` コマンドのコマンドライン引数として渡されるため、プロセステーブル上に一時的に表示されます。これはmacOS `security` CLIの制約です。

### アトミックな get_key()

`get_key()` は3つの操作を1回の呼び出しに統合します:

1. 見積もりコストが残予算（グローバル + プロバイダ + モデル）に収まるかチェック
2. そのコストを使用量ログにホールドエントリとして書き込み
3. Keychainからキーを取得

ステップ1が失敗した場合、ステップ2-3は実行されません。予算超過時は、bantoのAPIのみを使用するエージェントがキーを取得する手段はありません。

> **脅威モデルについて**: bantoは `get_key()` を経由してキーにアクセスするエージェントに対して有効です。シェルアクセスを持つエージェントがmacOS Keychainに直接問い合わせた場合、この制御を迂回できます。多層防御として、エージェントランタイム側でシェルアクセスを制限することを推奨します。

## カスタムバックエンド

シークレットの保管先は `SecretBackend` プロトコルにより差し替え可能です。`get`、`store`、`delete`、`exists`、`list_providers` メソッドを持つ任意のオブジェクトが使用できます。継承は不要です（構造的部分型）。

### 環境変数

```python
import os
from banto import SecureVault

class EnvVarBackend:
    """BANTO_KEY_<PROVIDER> 環境変数からAPIキーを読み取る。"""

    def get(self, provider: str) -> str | None:
        return os.environ.get(f"BANTO_KEY_{provider.upper()}")

    def store(self, provider: str, api_key: str) -> bool:
        os.environ[f"BANTO_KEY_{provider.upper()}"] = api_key
        return True

    def delete(self, provider: str) -> bool:
        return os.environ.pop(f"BANTO_KEY_{provider.upper()}", None) is not None

    def exists(self, provider: str) -> bool:
        return f"BANTO_KEY_{provider.upper()}" in os.environ

    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if self.exists(p)]

vault = SecureVault(caller="my_app", backend=EnvVarBackend())
```

### 1Password CLI

```python
import json
import subprocess
from banto import SecureVault

class OnePasswordBackend:
    """1Password CLI (op) を使用してAPIキーを取得する。"""

    def __init__(self, vault_name: str = "Private"):
        self.vault_name = vault_name

    def get(self, provider: str) -> str | None:
        try:
            result = subprocess.run(
                ["op", "item", "get", f"banto-{provider}",
                 "--vault", self.vault_name,
                 "--fields", "label=credential", "--format", "json"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return json.loads(result.stdout).get("value")
        except (subprocess.SubprocessError, OSError):
            pass
        return None

    def store(self, provider: str, api_key: str) -> bool: ...
    def delete(self, provider: str) -> bool: ...
    def exists(self, provider: str) -> bool:
        return self.get(provider) is not None
    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if self.exists(p)]

vault = SecureVault(caller="my_app", backend=OnePasswordBackend())
```

### インメモリ（テスト用）

```python
from banto import SecureVault

class InMemoryBackend:
    def __init__(self, keys: dict[str, str] | None = None):
        self._store = dict(keys) if keys else {}

    def get(self, provider: str) -> str | None:
        return self._store.get(provider)
    def store(self, provider: str, api_key: str) -> bool:
        self._store[provider] = api_key
        return True
    def delete(self, provider: str) -> bool:
        return self._store.pop(provider, None) is not None
    def exists(self, provider: str) -> bool:
        return provider in self._store
    def list_providers(self, known_providers: list[str]) -> list[str]:
        return [p for p in known_providers if p in self._store]

vault = SecureVault(
    caller="test",
    backend=InMemoryBackend({"openai": "test-key-12345"}),
)
```

完全な実装例は [examples/06_custom_backend.py](./examples/06_custom_backend.py) を参照してください。

## 詳細な使い方

### カスタムKeychainプレフィックス

既存のプレフィックスでキーが登録済みの場合:

```python
vault = SecureVault(
    caller="my_app",
    keychain_prefix="claude-mcp",  # "claude-mcp-openai" 等を使用
)
```

### カスタムデータディレクトリ

```python
vault = SecureVault(
    caller="my_app",
    data_dir="/path/to/usage/data",
)
```

### プロバイダの明示指定

設定のプロバイダ対応表にないモデルを使用する場合:

```python
key = vault.get_key(
    model="my-custom-model",
    provider="openai",
    input_tokens=1000,
    output_tokens=500,
)
```

### CostGuardの直接使用（シークレットストレージなし）

予算追跡のみを行う場合（ホールド/精算パターン）:

```python
from banto import CostGuard, BudgetExceededError

guard = CostGuard(caller="my_mcp")

# 予算をホールド（見積もりコストを予約）
hold_id = guard.hold_budget(model="dall-e-3", provider="openai",
                            n=1, quality="standard", size="1024x1024")
# ... APIコール ...

# 実コストで精算
guard.settle_hold(hold_id, model="dall-e-3", n=1, provider="openai", operation="image")
```

ホールドなしで使用する場合（後方互換）:

```python
guard.check_budget(model="dall-e-3", n=1, quality="standard", size="1024x1024")
# ... APIコール ...
guard.record_usage(model="dall-e-3", n=1, provider="openai", operation="image")
```

## 免責事項

bantoは予算管理を支援するツールであり、API利用料金の超過を保証するものではありません。同梱または利用者が設定した料金テーブルの不正確さ、ソフトウェアの不具合、設定の誤り、bantoのAPIを経由せずにAPIキーやプロバイダサービスにアクセスするエージェントやプロセスに起因する金銭的損害について、作者は一切の責任を負いません。利用者は、各プロバイダの課金ダッシュボードで実際のAPI利用料金を確認し、料金テーブルを最新の状態に維持する責任を負います。

詳細は [LICENSE](./LICENSE) をご参照ください。

## ライセンス

デュアルライセンス:

- **個人利用**（無料）: 個人の方は、個人・教育・研究目的で無償で利用・改変・再配布できます。
- **商用利用**（有料）: 企業・組織での利用には、[AllNew LLC](https://github.com/allnew-llc/banto/issues) との商用ライセンス契約が必要です。

詳細は [LICENSE](./LICENSE) をご参照ください。
