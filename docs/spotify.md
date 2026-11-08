# 自分のSpotifyアプリで使う

## 必要なもの

Spotifyの利用条件を満たす自分のアカウント、Soloist用APIキー、Web API用の自分のClient IDを用意します。
Soloistの認証とWeb APIの認可は別です。
公開リポジトリには著者のキー、Client ID、アカウント状態を入れていません。

Soloist本体とAPIキーの準備は[公式プロジェクト](https://github.com/spotify/soloist)の最新の案内に従ってください。
APIや利用可能な機能は提供元の変更を受けます。

## Web APIのPKCE認証

1. [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)で自分のWeb APIアプリを作成します。
2. Redirect URIを`http://127.0.0.1:8768/callback`として登録します。`localhost`とは別の設定です。
3. `examples/spotify-client.example.json`をリポジトリ外へコピーし、自分のClient IDに置き換えます。
4. PCのブラウザーが使える状態で、次の認証補助を起動します。
5. `http://127.0.0.1:8768/`を開き、読み取り権限への同意を自分で確認します。

```sh
python3 gui/app/auth_helper.py \
  --client-file "$HOME/.config/jtb1-spotify-client.json" \
  --state-dir "$HOME/.local/state/jtb1-spotify-auth"
```

stateディレクトリーは0700、認証ファイルは0600になります。
既存のトークンを自動上書きせず、認証コードの交換を自動再試行しません。
待ち受けは127.0.0.1だけで、最大30分または一回の認証で終了します。
SSH先で実行する場合、ブラウザーから同じloopbackへ到達できる構成は別途必要です。

`web-api-token.json`の内容、認可後のcallback URL、認証コード、refresh tokenをチャットやissueへ貼らないでください。
Client Secretはこのフローでは使いません。

## 端末側の設定

Web API Client IDは、環境変数`JTB1_SPOTIFY_CLIENT_ID`または`/etc/jtb1-gui/client-id`から読みます。
後者は32文字のClient IDだけを書いたファイルです。
Client IDは公開識別子ですが、自分のアプリのものを使います。
設定がない場合、Web APIへの要求は失敗します。

認証で作ったファイルは、信頼するUSB接続PCと転送方式で`/var/lib/jtb1-gui/web-api-token.json`へ移します。
親ディレクトリー0700、ファイル0600、GUI実行ユーザー所有とします。
旧ADBのPTY経由のshell標準入力へ秘密を直接流す方法は避けてください。
リポジトリ内や配布用アーカイブへコピーしないでください。

Soloist用APIキーは`/etc/jtb1-soloist/api-key`、認証状態は`/var/lib/jtb1-soloist/`へ置く元構成です。
音声ラッパーはキーをCLI引数として渡すため、`ps`の引数やプロセス情報を公開するとキーが漏れる可能性があります。
通常のデバッグログも公開前に確認してください。

## 日本語辞書とアイコン

漢字候補には読者が入手したSKK辞書を使います。
辞書がなくてもかな入力はできますが、元構成の漢字候補を再現しません。
変換方法とライセンスは[外部ファイルの説明](../THIRD_PARTY.md)を参照してください。

Spotifyアイコンは同梱していません。
公式素材の条件を確認して使用する場合の想定パスは`/opt/jtb1-gui/share/spotify/Spotify_Primary_Logo_RGB_Green.png`です。
欠落時もSpotifyの文字付きタイルは動きます。

## 動作範囲

Web APIは検索、保存曲、アルバム、フォロー中アーティスト、利用可能なプレイリストを取得します。
他人のプレイリスト内容などには開発用モードの制限があり得ます。
再生と停止、曲送り、音量はローカルのSoloistへ渡します。

ロスレスを要求する設定は配信品質の上限であり、実際の受信コーデックを証明しません。
S32_LEのPCM出力も、入力がロスレスである証拠にはしないでください。
