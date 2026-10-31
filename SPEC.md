# Point Cloud Export (PCB) — 仕様書

Blender 上の点群を外部に書き出すアドオンの仕様。
Point Cloud Base ツールロードマップ **STEP2「共有」** に相当する。

- リポジトリ: `blender-pointcloud-export`（LAS Importer とは**別アドオン／別リポジトリ**）
- 対象 Blender: **4.2 〜 5.x**
- 実装言語: Python + numpy（Blender 同梱）。**外部 wheel なし＝全OS対応**
- ステータス: **v0.2 — モード①(PLY)・②(HTMLビューア) 両方実装済み**

---

## 1. 目的 / 背景

Blender に取り込んだ点群（LAS Importer 等で読み込んだもの）を、Blender 内で
整理・編集したあと「外に出す」ための手段を提供する。用途は2つに分かれる。

| モード | 出力 | 目的 | 渡す相手 |
|---|---|---|---|
| **① データ書き出し** | `.ply` | 編集済み点群を他ソフトへ受け渡し | CloudCompare / CAD / 自分 |
| **② Web ビューア書き出し** | 単一 `.html` | インストール不要で見せる | 施主・現場・非エンジニア |

どちらも **Blender 内の同じ点群（座標＋色）を読む**だけで、書き出し先が違う。
よって 1 アドオンに 2 モードを持たせる。

**実装順: ① PLY を先行リリース → ② HTML ビューアを後続。**
（①は軽量かつニーズが即物的に強い）

---

## 2. 設計判断（確定事項）

1. **LAS Importer とは別アドオン・別リポジトリ**
   役割が違う（取り込み vs 書き出し）。独立してバージョン管理・リリースする。
   ブランドは共通（PCB）で揃える。将来ツールが増えたら「PCBツール群」への
   統合を検討。

2. **色は必須（フォールバック付き）**
   出力には必ず RGB を含める。元データに色が無い場合もフォールバックで着色し、
   「真っ黒／真っ白で何も見えない」状態を防ぐ。

3. **モード② は「オフライン単一 HTML」**
   three.js を HTML にインライン同梱し、データも埋め込む。ネット・サーバー・
   インストールすべて不要。測量・建設現場のオフライン環境でも確実に開く。
   （詳細は §4 の `file://` 問題を参照）

4. **数百万点に対応**
   three.js は点群を 1 バッファ＝1 ドローコールで描くため、数百万点は GPU 的に
   余裕。ボトルネックは描画ではなくファイルサイズ（§4 参照）。

---

## 3. モード① PLY 書き出し仕様（実装済み）

### 入力
- Blender の **Mesh オブジェクトの頂点** および **ネイティブ Point Cloud
  オブジェクト** を点とみなす（LAS Importer は Point Cloud 型で取り込むため
  両対応が必須）。
- 色は **アクティブな color attribute（POINT ドメイン）** から読む。Point Cloud
  には `active_color` が無いため先頭の color attribute にフォールバックする。
- color attribute はリニア値で返るため、出力時に **リニア→sRGB 変換**してから
  8bit 化する（CloudCompare 等で元の見た目を再現）。

### 処理
1. 対象オブジェクトを収集（選択のみ／シーン全体を切替可）。
2. 各 Mesh の頂点座標を `foreach_get("co")` で一括取得。
3. `Apply Object Transform` が ON のとき、各オブジェクトの `matrix_world` を
   座標にベイク。
4. 色を取得（無ければフォールバック）。
5. 複数オブジェクトは**1つの PLY に結合**して書き出し。

### 出力フォーマット
バイナリ・リトルエンディアン PLY。CloudCompare / MeshLab が直接読める。

```
ply
format binary_little_endian 1.0
element vertex N
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
<binary: 各点 float32 x,y,z + uint8 r,g,b>
```

### 色フォールバック
- color attribute が無い → **均一のライトグレー (200,200,200)**。
- （将来）intensity があればグレースケール、無ければ高さ(Z)グラデーション。

### UI / オプション
- メニュー: **File → Export → Point Cloud (.ply) — PCB**
- `Selection Only`（既定 ON）: 選択オブジェクトのみ
- `Apply Object Transform`（既定 ON）: ワールド変換をベイク
- 完了時に書き出し点数を INFO 表示。

### v0.1 既知の制限
- 間引きなし（大規模点群はフルサイズで出力）。
- 色フォールバックは均一グレーのみ。
- LAS/LAZ 書き出しは未対応（§6）。

---

## 4. モード② Web ビューア書き出し仕様（実装済み）

### 体験の核
書き出した `.html` を **ダブルクリックするだけ**で点群をぐりぐり閲覧。
サーバー・インストール・ネット、すべて不要。

### `file://` 問題（設計の肝）
ブラウザは `file://` で開いた HTML から**別ファイルの `fetch()` を CORS で
ブロック**する。「index.html + data.ply」の2ファイル構成はダブルクリックで
動かない。
→ **データを HTML に埋め込んだ単一ファイル**にする。three.js も**インライン
同梱**し、完全オフライン動作を保証する。

### データ形式
1 点あたり **9 バイト**に量子化して埋め込む。

| 項目 | 形式 | バイト |
|---|---|---|
| 座標 X/Y/Z | 16bit 量子化（バウンディングボックス内で正規化） | 6 |
| 色 R/G/B | 8bit | 3 |

- バイナリに固めて **base64** で `<script>` に埋め込む。
- ビューア側でバウンディングボックスから座標を復元（dequantize）。

### ファイルサイズ目安

| 点数 | 生データ(9B/点) | base64 後の HTML |
|---|---|---|
| 100万 | 9 MB | 約 12 MB |
| 300万 | 27 MB | 約 36 MB |
| 500万 | 45 MB | 約 60 MB |

- 実用の快適ライン = **〜300万点**（メール添付は厳しいが Drive/USB は余裕）。
- 書き出し時に**間引きオプション**で軽量化可能。
- 将来 **Draco 圧縮**でサイズ 1/2〜1/3（デコーダ同梱、v2 候補）。
- **1000万点超**で初めてオクトツリー/LOD が必要（さらに先の STEP）。

### ビューア機能（v1 想定）
- three.js `Points` + `PointsMaterial` で描画。
- 操作: オービット / パン / ズーム（OrbitControls）、読み込み時に自動フィット。
- UI: 点サイズスライダー、背景色切替、ビューリセット、点数表示。
- 後回し: 計測、断面、複数オブジェクト同時、スクショ出力、LOD。

### 色
モード①と同じ扱い（必須・フォールバック付き）。

---

## 5. 技術スタック / アドオン構成

| 層 | 選定 |
|---|---|
| 書き出し全般 | Blender Python (`bpy`) + numpy（同梱） |
| PLY | 自前バイナリライタ（依存なし） |
| HTML ビューア描画 | three.js（HTML にインライン同梱） |
| ビューアデータ | 自前量子化バイナリ → base64 埋め込み |
| 配布形態 | PLY は `.ply` / ビューアは単一 `.html` |

### ファイル構成（リポジトリ = アドオン本体）
```
blender-pointcloud-export/
  blender_manifest.toml   # Blender 拡張マニフェスト (schema 1.0.0)
  __init__.py             # オペレータ + メニュー登録
  README.md
  SPEC.md                 # 本書
  .gitignore
```

### マニフェスト要点
- `id = "pcb_pointcloud_export"`, `type = "add-on"`
- `blender_version_min = "4.2.0"`（上限なし＝5.x 対応）
- `license = ["SPDX:GPL-3.0-or-later"]`（bpy 利用のため GPL 必須）
- **wheels / platforms 指定なし**（純 Python のため全OS）

### ビルド
```
blender --command extension build
```
→ `pcb_pointcloud_export-<version>.zip` を生成（git 管理外）。
配布は GitHub Releases（LAS Importer と同方式）。

---

## 6. ロードマップ / 将来拡張

- **LAS/LAZ 書き出し（v2 候補）**
  LAS は UTM 等の巨大座標。取り込み時に Blender は原点付近へオフセットして
  いるため、正しく書き戻すには **CRS / オフセットの復元**が必要。
  → LAS Importer 側に「適用したオフセットをメタデータ保持」させておくと、
  後で LAS 往復がきれいに実装できる（**要・importer 連携**）。
- モード② HTML ビューア本実装。
- 間引き・高さ/intensity フォールバック・Draco 圧縮・LOD。
- XYZ/PTS テキスト書き出し（要望次第）。

---

## 7. 未決事項 / 確認したいこと（解決済み）

1. **解決**: LAS Importer は **ネイティブ Point Cloud オブジェクト**で取り込む
   （Mesh ではない）。color attribute 名 `Col` / POINT ドメイン / FLOAT_COLOR /
   値域はリニア 0–1。→ エクスポータは Mesh と Point Cloud の両方に対応し、
   リニア→sRGB 変換して出力する。
2. **解決**: FloatColor（FLOAT_COLOR）。`foreach_get("color")` はリニア値を
   そのまま返す（検証: 0.5 を set→get で 0.5）。ByteColor も同様にリニア扱い。
3. **解決**: three.js **r128（classic / 非モジュール版）** をインライン同梱。
   ES モジュール版は `file://` の CORS で読めないため、UMD の classic build と
   classic 版 OrbitControls を使う（単一ファイルで確実にオフライン動作）。
4. **解決**: 間引き既定 = **300 万点**（`Max Points` で変更可、0 で全点）。

### 残課題（将来）
- PLY 側の間引きオプション、高さ/intensity フォールバック、Draco 圧縮、LOD。

---

_Point Cloud Base — https://pointcloudbase.com/tools_
