# JT-B1 Linux toolkit

Panasonic TOUGHPAD JT-B1で、純正Linux 3.0.8+を残したAlpineとSpotifyプレーヤーを作ったときのソースコード集。
日本語入力付きGUI、共通ステータスバー、物理ボタン、音声経路監視、Wi-Fi互換処理、MC7700のpovo接続サービスを収録しています。

**これはAndroidをLinuxへ変える一括インストーラーではありません。**
端末がなくても、PC上のテストと画面プレビューから試せます。
実機向けコードは、すでにLinuxが起動しているJT-B1を対象とします。

## root取得とSIMロック解除のコード

Linux化の入口になった**dmmdのroot取得用exploit**と、povo接続を可能にした**MC7700のSIMロック解除コード**を[research/](research/README.md)に収録しています。
実機で成功したコードの由来、公開向けの変更、前提条件、実行による影響は同ページに記載しています。
通常のPCテストでは実機に接続せず、解除操作やexploitを実行しません。

## PCで試す

LinuxのPC、Python 3.10以降、Cコンパイラー、make、日本語フォントを使います。
UbuntuやDebianでは次の手順で準備できます。
依存パッケージの導入には管理者権限を使いますが、テストとプレビューは通常ユーザーで実行してください。

```sh
sudo apt install python3-venv build-essential fonts-noto-cjk linux-libc-dev
git clone https://github.com/chun37/jt-b1-linux.git
cd jt-b1-linux
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make check PYTHON=.venv/bin/python
```

テストは実機、ADB、Spotifyアカウント、携帯通信を使いません。
認証サーバーの検査ではPCのloopbackに一時的に待ち受けます。
Cのテストも合成入力と自己検査だけを実行します。

ホーム画面のプレビューを生成できます。
出力先は新しいファイルを指定してください。

```sh
.venv/bin/python tools/render_home.py --output /tmp/jtb1-home-preview.png
```

プレビューは600×1024のPNGで、ネットワーク状態と電池残量は合成値です。
公式Spotifyアイコンは同梱していないため、ラベル付きタイルとして表示します。
日本語フォントの場所が異なる場合は`--font`を指定します。

## 収録内容

| ディレクトリー | 内容 | 実行による変更 |
| --- | --- | --- |
| `gui/` | Web API、PKCE認証、日本語入力、描画、ホーム、ボタン、起動時再生と音量保持 | 実機GUIは画面、入力、Spotifyの再生と音量、ローカル設定を扱う |
| `audio/` | Soloist起動、出力切替、挿抜監視 | 音声ミキサー、プロセス、再生状態を変更する |
| `wifi/` | 旧nl80211互換処理、JP地域設定、Wi-Fi監督 | モジュール、無線設定、IP、DHCPを変更する |
| `cellular/` | GobiNet WDS、povo接続、携帯限定HTTPS検査 | RF、APN、IP、経路、DNSを変更し、通信料金が生じ得る |
| `display/`、`touch/`、`tools/` | fb初期化、電池LED、タッチ確認、辞書変換、PC向け補助 | 用途別。実機向け表示コードは画面やLEDを書き換える |

## 用途別の入口

1. **コードを読む、PCで確認する**：[構成と検証範囲](docs/architecture.md)。
2. **すでにLinux化した端末へ移す**：[実機の前提条件と配置](docs/device.md)。
3. **自分のSpotifyアプリで認証する**：[Spotifyの設定](docs/spotify.md)。
4. **日本語変換辞書を作る**：[外部ソフトウェアと素材](THIRD_PARTY.md)。
5. **不具合を報告する**：[公開してよい情報](SECURITY.md)。

## 公開していないもの

rawパーティションへの書き込み、モデムFW書き込み、NVM編集、実験中に失敗した試行は含めていません。
root取得とSIMロック解除の研究コードは`research/`へ分離して公開しています。
そのため、このリポジトリだけで工場状態のAndroidから記事の最終構成を完全再現することはできません。

メーカーのカーネル、ドライバー、ファームウェア、Androidイメージ、Soloist本体、glibcやPulseAudioのバイナリーも同梱していません。
元ログ、バックアップ像、個体のIMEIやMAC、Wi-Fi認証情報、Spotifyの認証情報も含めていません。

## 検証した構成と制限

元の実機はJT-B1APAAABJ、純正カーネル3.0.8+、Alpine 3.23.7 armv7です。
元の構成では表示、5 GHz Wi-Fi、タッチ、日本語検索再生、スピーカーとイヤホンの発音、povo LTE、通常再起動後の復帰を確認しました。
公開時にClient ID設定、合成fixture、PC補助ツール、シリアル所有者の扱いを整理しており、**この公開版を実機へ再配置した試験はしていません**。
PCテストの成功を実機動作の保証とは扱いません。

イヤホンを含む音量上限は100%です。
AUX用途に合わせた元の仕様であり、聴覚保護の上限ではありません。
起動時再生は保存音量を復元するため、耳に装着したまま起動しないでください。
電源管理、サスペンド、長時間運転、移動中のLTE切替、全体バックアップからの復旧は未検証です。

Soloistの採用ビルドには90日で失効する制限があり、Web APIの利用条件も変わり得ます。
利用には各提供元の規約とライセンスの確認が必要です。
独自コードのライセンスは[MIT](LICENSE)です。
