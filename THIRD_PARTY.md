# 外部ソフトウェアと素材

このリポジトリのMITライセンスは、ここで配布する独自コードに適用します。
外部のバイナリー、辞書、フォント、ロゴへライセンスを付け直すものではありません。
下記ファイルは同梱せず、利用者が提供元の条件に従って入手する前提です。

## Spotify

[Spotify Soloist](https://github.com/spotify/soloist)の本体、SDK、APIキー、認証状態は配布しません。
利用条件、対応環境、ビルド期限は公式資料を確認してください。
[Spotify Web API](https://developer.spotify.com/documentation/web-api)のアプリ登録と利用権限も利用者が用意します。

アイコンは[公式ブランド素材](https://newsroom.spotify.com/media-kit/logo-and-brand-assets/)から、条件を確認して別途入手してください。
リポジトリのテストでは合成画像を使い、Spotify公式画像を再配布しません。

## フォントと日本語辞書

フォントは[Noto CJK](https://github.com/notofonts/noto-cjk)を別途使います。
フォント自体のライセンスを確認してください。
PCテストの標準パスは`/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`です。

漢字候補には[SKK辞書](https://github.com/skk-dev/dict)のSKK-JISYO.Lを別途入手できます。
辞書のライセンスと著作権表示を確認し、生成したSQLiteを他人へ渡す場合も元の条件を引き継いでください。
このリポジトリは辞書も生成済みSQLiteも配布しません。

```sh
python3 tools/build_dictionary.py /path/to/SKK-JISYO.L /tmp/kana.sqlite
```

入力はEUC-JPを前提にします。
Lisp形式は評価せず、かなの読みと通常の候補文字列だけを抽出します。
出力先が存在する場合は拒否します。
端末での想定配置は`/opt/jtb1-gui/share/kana.sqlite`です。

## OS、ドライバー、ランタイム

Panasonicのカーネル、Androidソフトウェア、ドライバー、firmware、NVS、モデムFWは同梱しません。
自分の端末に適合する正規の配布物とライセンスを確認してください。
別機種や別カーネルのモジュールを流用する手順ではありません。

glibc、ALSA、PulseAudio、Python、Pillow、libnlも同梱しません。
それぞれの提供元の利用条件と、再配布時のソース提供などの義務を確認してください。

Wi-Fiの互換コードはLinuxの公開UAPIを使います。
`setreg_jp.c`の固定規則は調査時のwireless-regdbのJP規則から保守的な範囲を参照したもので、現行法令や認証条件の保証ではありません。
メーカーやSpotifyによるサポート、承認を表すリポジトリではありません。
