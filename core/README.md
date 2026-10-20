# banto-core

Banto の broker transport、固定宛先 HTTP、監査ログ、Keychain、sealed signing、
P-256 検証を共有するパッケージです。Python 3.10 以降、標準ライブラリと
macOS Security framework だけを使います。Banto アプリケーションは import しません。

## 開発と配布

このディレクトリは独立した Python project です。`banto_core` を import します。
`python -m build core` で wheel と source distribution を作れます。
本家 banto は `banto-core==0.1.0` に依存します。モノレポ開発では
`uv sync` が `tool.uv.sources` に従ってこのディレクトリを使用します。
pip のローカル開発では `pip install ./core .` を使用してください。

**マージ保留**: AllNew が PyPI の正規化名 `banto-core` を所有し、レビューした
0.1.0 の wheel/sdist を公開して所有者と artifact の対応を確認するまで、
core に依存する Banto の PR をマージしてはいけません。名前の404応答や版番号の
一致だけで所有を確認したことにはしません。TestPyPI への登録も代用しません。
所有者・公開 URL・レビュー対象 SHA と配布物の hash を本人が確認します。
現時点は未確認で、PR は Draft のままです。自動の公開や名前の確保はしません。
`banto_core` の版は最初の読込み時にも照合しますが、これは真正性の証明ではありません。
同じ 0.1.0 を名乗る第三者パッケージを信頼してよい理由にはなりません。

未公開の core を必要とする banto リリースを先に公開してはいけません。
core の署名付き tag、配布先、公開は所有者の別承認で行います。この PR は
インストール済みの Banto や LaunchAgent を更新しません。

## 境界

- broker は同じ UID の local socket を扱います。同じ UID の悪意あるプロセスを
  隔離する機構ではありません。別 UID と不正な socket 権限を拒否します。
- 操作の許可表は利用側が実装します。Banto の MCP、予算、sync、lease は含みません。
- KeychainStore の新しい利用側は guard と秘密値観測 callback を両方注入します。
  未接続の既定 guard は読み取り・書き込み・削除を拒否します。生の値を返す
  API は broker 内部用です。エージェントのプロセスへ公開してはいけません。
- SealedSigner(tag_prefix=...)、runtime directory、LaunchAgent label/module、
  AuditLog(path) で利用側の領域を選べます。Banto の既定値は変更しません。
- HTTP は OpenAI Responses / Anthropic Messages の固定宛先だけです。
  proxy を無視し、redirect を拒否し、8 MiB を超える返答を拒否します。
  decode 後の返答から使用した秘密値を除去します。
- AuditLog は許可された metadata のみ記録します。利用側が prompt や秘密値を
  metadata の許可フィールドへ流し込まないことも必要です。

## 検証

`core/tests` はダミー値と mock を使う単体試験です。実 Keychain、Touch ID、
provider API、launchctl を呼びません。既存 `tests/` は変更せずに比較します。
モノレポのルートでは `make verify PYTHON=<pytest/MCP入りPython>` が
core の単体試験と既存 suite を実行します。既存 suite の原本 41 ファイルは
`compatibility-tests.json` の SHA と照合します。`core/tools/legacy_tests.py` は
実 Keychain・外部接続・未許可コマンドの呼出しを検出し、原本のダミー lease 試験に必要な
固定コマンドと local fixture 接続だけを許可します。shell 内のパスは安全な ASCII 文字・
絶対正規化パスに限定し、実行ファイル・argv・cwd・PATH と loader 環境変数も照合します。
git の許可形は `git -c core.fsmonitor=false check-ignore ...` に固定します。
これは偶発的な副作用を検出する guard であり、意図的な同 UID コードへの OS 隔離では
ありません。実機試験用の probe は
collection 前に無効化します。ログと HOME/cache は `work/verification/`、
空白を含むパスを扱えない原本 shell 試験の小さい一時データは `/tmp` に作り、
試験後に消します。固定 port があるため、baseline と候補は直列で実行します。

実 Secure Enclave の試験は本人が操作する別工程です。安全 runner による skip を
実機合格や C5 全合格として扱ってはいけません。

ライセンスは同梱の LICENSE（本家と同じ Dual License）を参照してください。

## 既存実装からの修正点

観測済み秘密値が BrokerError の文言や監査 metadata の文字列へ混入する場合も、
観測集合を破棄する前に除去します。既定名や正常応答を変えず、例外応答と
監査ログの値除去を補強する修正です。ダミー値を使った socket 試験で確認します。
tuple 内の観測値も除去し、tuple の型は維持します。固定 HTTP transport は未知の
provider を外部通信前に拒否します。この2点は抽出元の内部 helper に対する安全側の
挙動変更です。Banto 公開 API の provider allowlist は以前から存在します。

## Phase 3 の受入れ

本人が実機2件の作成・署名・検証を確認し、元から存在する sealed 鍵の発見と
socket・Keychain service prefix・LaunchAgent label・sealed tag の互換を実機確認します。
元の非抽出試験にある pool 引数欠陥の修正は別 PR で扱い、この PR の原本41試験は
変更しません。mock/静的照合や安全 runner の skip を実機確認の代用にしません。
PyPI の上記確認、本人のマージと tag まで Phase 3 は未完了です。
grader の OS 隔離は Phase 5 の別条件として管理します。
