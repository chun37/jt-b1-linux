# 実機の前提条件と配置

## この手順で行わないこと

この説明は、すでに純正3.0.8+からAlpineを起動できるJT-B1を対象にします。
Androidからのroot取得、bootargsの変更、p22へのrootfs書き込み、SIMロック解除は扱いません。
別機種へコピーする手順でもありません。

`tools/stage.py`はPCの新しいディレクトリーへソースを並べるだけです。
生成物はrootfsや起動イメージではなく、そのまま端末の`/`へ上書きする用途ではありません。

## 必要な外部ファイル

| 機能 | 別途準備するもの |
| --- | --- |
| GUI | ARMv7で実行できるPython、Pillow、日本語フォント。元構成は私有Python 3.12.16とPillow 11.3 |
| 音声 | 自分のAPIキーとSoloist、私有glibc/PulseAudio/ALSA、ALSA設定とPulseAudioモジュール |
| Wi-Fi | 同一カーネルのcompat/cfg80211/mac80211/wl12xx/wl12xx_sdioと正規の対応firmware/NVS |
| 携帯 | SIM解除済みMC7700、同一カーネルのsierra/GobiNet、povo SIM、有効な通信契約 |
| 表示と入力 | 実機のfb0、maXTouch、純正LEDと電池sysfs。任意のfbdevへ流用しない |

元の音声構成は`/opt/jtb1-soloist/lib/ld-linux-armhf.so.3`を直接起動します。
`lib/`にglibcと依存ライブラリー、`bin/`にpulseaudio/pactl/amixer、`modules/`にPulseAudioモジュール、`alsa/`に設定を置く前提です。
現代のtime64 ALSAをそのまま入れると旧カーネルのPCM ioctlで失敗しました。
元の私有ALSAは1.2.14を専用glibc 2.41 sysrootで`-D_TIME_BITS=32 --disable-year2038`としてビルドしたもので、2038年以降の動作は保証しません。
ランタイムの自動ダウンロードや完全な再構築スクリプトは同梱していません。

## ソースのステージング

PCで、まだ存在しない出力ディレクトリーを指定します。
通常ユーザーとして実行します。

```sh
python3 tools/stage.py --output ./staged-root
```

主な配置は次のとおりです。
ステージングは設定、ドライバー、外部ライブラリーを補いません。

| ソース | 想定配置 |
| --- | --- |
| `gui/app/*.py`、`gui/bin/*` | `/opt/jtb1-gui/app/`と`/opt/jtb1-gui/bin/`。`jtb1-gui`だけ`/usr/local/sbin/` |
| `audio/jtb1-soloist`、`audio/jtb1-audio-watch` | `/usr/local/sbin/`と`/usr/local/libexec/` |
| `wifi/jtb1-wifi`、`wifi/jtb1-regulatory` | `/usr/local/sbin/`。互換.soは`/usr/local/libexec/` |
| `cellular/povo_service.py`と補助モジュール | `/usr/local/libexec/jtb1-povo-service.py`と`/usr/local/lib/jtb1-povo/` |
| `display/jtb1-battery-led` | `/usr/local/sbin/jtb1-battery-led` |

`gui/bin/jtb1-gui-python`は元構成の私有muslランタイムを参照します。
`/opt/jtb1-gui/runtime/lib/ld-musl-armhf.so.1`と`runtime/usr/bin/python3`などを用意するか、移植先で確認したPython起動用ラッパーに差し替える必要があります。
システムのPythonを使う構成を実機確認済みとみなさないでください。
フォントの想定パスは`/opt/jtb1-gui/share/NotoSansCJK-Regular.ttc`です。

端末へ転送する前に既存サービスの所有者、ミキサー、起動設定を保存し、置換対象のサービスを個別に停止する必要があります。
ステージングツリー全体の上書きや、稼働中ソースへの直接コピーは勧めません。
このリポジトリはその機種固有の導入処理を自動化しません。

## Cコードのビルド

PCの`make c-test`でできるバイナリーはPC用です。
そのままARM端末へ転送しても動きません。
対象ランタイムに合うクロスコンパイラーとsysrootで、別の作業コピーまたは出力先を使ってビルドしてください。

```sh
make CC=arm-linux-gnueabihf-gcc build/fb-present build/touch-feedback build/nl-scan-compat.so build/setreg-jp.so
```

この例のglibc向けコンパイラーがAlpine musl用として適合するとは限りません。
元構成では対象環境に合うARMツールチェーンを使いました。
`setreg-jp.so`は実行時のlibnlのシンボルを参照し、古いnl80211 ABIとJP規則を前提にします。
`.so`をシステム全体にpreloadしないでください。

## 表示とタッチの注意

`fb-present`は引数なしでもfb0を開きます。
`--present IMAGE.bgra`は画素を書き、`--reconfigure IMAGE.bgra`は現在と同じモードを強制再設定します。
画像は1024×600、32bit BGRAの2,457,600バイトに限定されます。

`touch-feedback`は通常実行で画面へ十字を描きます。
GUIと同時に実行すると描画が競合します。
`--selftest`は実機を使わず、`--probe`は入力装置の情報を取得します。
`tools/inspect_device.py`はホワイトリスト化したOS、画面、電池の情報だけを読みます。
危険な読み出しを含むsysfs全体の走査はしません。

## Wi-Fiと携帯サービスの注意

Wi-Fiの設定ファイルは`/etc/wpa_supplicant/wpa_supplicant.conf`、0600を前提にします。
`/etc/jtb1-wifi/mac-address`には自分の端末に割り当てたアドレスを用意します。
他人のMAC、NVS、認証設定をコピーしないでください。
`jtb1-wifi`は切断時にwlan0のIPv4を消し、DHCPを管理します。
既存ネットワーク管理サービスと同時起動しないでください。
JP固定の地域設定を日本以外で流用せず、現地の法令と機器条件を確認してください。

`povo_service.py`はSIM解除を行わず、通常起動中のモデムに対してAPNとRF、データ接続を設定します。
`usb0`のdefault route metric700と`/etc/resolv.conf`の識別ブロックを変更するため、既存管理方式との整合性を確認します。
初期値ではシリアルポートを使う別プロセスがいれば失敗します。

元構成のUSBシリアルブリッジを一時停止する互換処理は`JTB1_ALLOW_BRIDGE_PAUSE=1`でのみ有効になります。
この処理はttyUSB/ttyGSを保持するshとcatへSIGSTOP/SIGCONTを送るため、複数のUSBシリアル機器を使う環境では有効にしないでください。
ATポートはVID:PID 1199:68a2のinterface03に絞っています。

携帯通信の検査は、明示的に同意した場合だけ実行します。
通信料金が発生し得ます。
出力にはその接続で割り当てられたIPが含まれるため、ログをそのまま公開しないでください。

```sh
python3 cellular/verify_cellular_https.py --confirm-network-test
```

## 自動起動は手動検証の後に行う

このリポジトリは`inittab`を自動編集せず、OSの再起動もしません。
GUIの起動処理には音声サービスの遅延起動と保存音量での自動再生があるため、設定前に`boot_playback.py`を確認してください。
初期音量20%は上限ではなく、保存値があれば0〜100%で復元します。
ホームはアプリ終了時や電源短押しの戻り先です。
電源管理や長押し動作を移植したものではありません。
