# JT-B1のroot取得とMC7700のSIMロック解除

ネイティブLinux化へ進むために使ったroot取得コードと、povo接続を可能にしたSIMロック解除コードを公開します。
いずれも所有者の許可を得た端末を対象とする研究用コードです。
通常のGUIサービスからは呼び出しません。

## root取得

`root-dmmd/client_file.c`は2026-11-04にJT-B1の純正Androidビルド09.0101でuid 0を確認したクライアントのソースそのものです。
Dirty COWではなく、メーカー管理デーモン`dmmd`のコマンドインジェクションを利用します。
`DeviceManagementService`のトランザクション12がrootのデーモンを起動し、受け取った`-dmsdon`の引数をエスケープせず`system()`へ渡す経路を利用しています。
ペイロードは`rootrun.py`の`PREFIX`と`payload()`にあります。

```sh
make build/dmmd-client
python3 research/root-dmmd/rootrun.py --help
```

クロスコンパイルには`arm-linux-gnueabihf-gcc`を使います。
Pythonラッパーは公開向けに追加したもので、実機では再実行していません。
明示的なADBシリアル、純正ビルド、shell UID、既存ファイルの不在、管理デーモンの停止を検査します。
非loopbackの全ネットワークインターフェースを手動でDOWNにしてから使う設計です。
`dmmd`はINADDR_ANYにbindするため、ネットワークを有効にしたまま起動すると端末外から到達される可能性があります。

```sh
python3 research/root-dmmd/rootrun.py --serial YOUR_ADB_SERIAL --execute
```

既定のrootコマンドは`id > /data/local/tmp/jtb1-root-id.txt`です。
ADB終了コードだけでroot成功とは判定せず、生成ファイルを個別に確認してください。
1回のgetter呼び出しだけを行い、自動再試行しません。
ポート競合時はデーモンが別ポートへ移るため、この固定ポートクライアントは失敗し得ます。
停止状態を確認できない場合はネットワーク隔離を維持してください。
作業ファイルは自動削除せず、恒久的なsu導入やLinuxインストールは行いません。
ネイティブLinux化済みの端末には使いません。

## MC7700のSIMロック解除

`mc7700-unlock/probe_gsdi_read.py`と`unlock_network_once.py`は、2026-11-06の実機成功スクリプトを公開用に整理したものです。
成功時のモデムはMC7700、ファームウェアは`SWI9200X_03.05.19.00Aap`でした。
成功した元コードと、以下の公開版変更を区別してください。
公開版は実機で再実行していません。

1. 絶対パスで読み込んでいたフレーム関数を`diag_frames.py`へ分離しました。
2. 明示フラグとroot権限の検査を追加し、importと`python -O`での実行を拒否します。
3. ローカル環境専用の後処理監査2本を削除しました。

標準ライブラリーのみを使い、Linux上でモデムのUSBシリアルへ直接接続します。
DIAGはUSB interface 00、ATはinterface 03を探します。
元の単一モデム環境向けの検出であり、VID/PID照合や複数モデムの安全な選択は実装していません。
**他のUSBシリアル機器を外し、接続対象とポート所有者を確認してから使ってください。**
GUI、通信サービス、ブリッジの同時利用を避けてください。
元コードはシリアルを保持する`cat`と`sh`を一時停止し、最後に開始時刻を照合して再開します。
その他のポート利用者の検出や排他制御は完全ではありません。
通常のPC向け自動解除ツールではありません。

```sh
cd research/mc7700-unlock
python3 probe_gsdi_read.py --execute-on-owned-device
python3 unlock_network_once.py --execute-on-owned-device
```

1本目は遅延GSDI要求`0x80 / subsystem 33 / command 0x48`を使います。
内部サブコマンド`0x42`で状態を確認し、成功時だけ`0x44`でNW解除キーを読みます。
legacy direct `0x44`のEFS書き込みとは別の要求です。
IMEIとキーは標準出力に表示せず、`/tmp/gsdi-nw-key.private`へ0600、排他的作成で保存します。
このファイルは秘密情報であり、Gitやissueへ添付しないでください。

2本目は秘密ファイルの所有者、モード、IMEI一致、機種、FW、PNロック、PH-NET PIN状態を確認します。
永続化を伴う`AT+CLCK="PN",0,"<同一IMEIの取得済みキー>"`を1回だけ送ります。
送信前に`/tmp/gsdi-nck-used.once`を排他的に作成します。
その後PN解除とSIM READYを照合し、失敗時も自動再試行しません。
このマーカーは同じファイルシステム上での二重実行防止であり、再起動や削除をまたぐ保証はありません。
キーの推測、総当たり、FW書き込み、NVM編集は行いません。
誤った解除操作には試行回数消費や通信不能のリスクがあります。
成功しても全SIM、周波数帯、通信事業者の動作を保証しません。

## 公開範囲と検証

メーカーのバイナリー、実機ログ、IMEI、解除キー、バックアップ像は含めません。
このディレクトリーの独自コードもリポジトリーのMITライセンスに従います。
逆アセンブル結果やメーカーソースを転載せず、研究時に作成したクライアントとプロトコル実装だけを収録しました。
PC検査はフレームCRC、要求バイト列、ペイロード制限、実行ガードを対象とし、実機成功の再現試験ではありません。

```sh
python3 -m unittest discover -s tests/research -v
```
