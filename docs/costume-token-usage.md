# 衣櫃外觀用到的顏色變數（token）

由 `python scripts/build_costumes.py tokens --write` 產生，**不要手改**（`tests/test_costumes.py` 會比對是不是最新的）。
給 SYNOX 核心做「套用前缺漏檢查」：一套外觀的 theme.css 與 slot SVG 用到的 `var(--…)`，在套用時都要有值。

核心認得的 token 來源：SYNOX 外觀合約第 1 版（synox/theme/contract.py）。

## 怎麼讀

- **核心 token**：合約上的 12 色＋`--radius`、`--font-ui`、`--font-brand`。每一套的 pack.json 淺色／深色都給齊（`check` 會擋）。
- **吉祥物 token**：`--m1`、`--m2`、`--mi`、`--mb`（選用）；pack.json 有給就用，沒給 SYNOX 從核心色算（`DERIVED`）。
- **進階 token**：合約的進階名字（`--m-hi`、`--surface-2`…）；外觀包通常不給，SYNOX 從核心色算。
- **自己的 `--th-`**：這一套在 theme.css 的 `:root` 自己宣告（淺色、深色各一份），不需要核心提供。
- **區域變數**：規則裡自己設的（`--i`、`--n` 這種排第幾個的數字），不是顏色。
- **寫死的顏色**：不經變數、直接寫在 SVG 屬性或 theme.css 規則裡的顏色（`var(--x, #後備)` 的後備值不算）。
  吉祥物與 App 圖示（`mascot.*`、`appicon`）裡的多半是刻意的配色（高光、腮紅、陰影），換深淺色也不變；
  開場、字標裡的要特別看。

## 要注意的

- 沒有：每一套用到的變數都是核心 token、pack.json 有給的吉祥物 token、自己宣告的 `--th-`，或規則裡的區域變數。

## 總表

| 外觀 | 版本 | 核心 token | 吉祥物 token | 進階 | 自己的 `--th-` | 區域變數 | 不認得 | 寫死的顏色（吉祥物／開場與字標） |
|---|---|---|---|---|---|---|---|---|
| `costume-a-bubble` 方方 | 1.0.0 | `--bg` `--line` `--muted` `--ok` `--soft` `--surface` `--text` `--warn` | `--m1` `--mb` `--mi` | — | — | `--i` | — | 8／1 |
| `costume-b-lantern` 提燈 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | `--i` | — | 8／2 |
| `costume-c-stamp` 小印 | 1.0.0 | `--accent` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mi` | — | — | — | — | 5／0 |
| `costume-d-snail` 小殼 | 1.0.0 | `--accent` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mi` | — | — | `--i` | — | 6／1 |
| `costume-e-cursor` 游標 | 1.0.1 | `--accent` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mi` | — | — | `--i` | — | 6／0 |
| `costume-f-mochi` 糰糰 | 1.0.0 | `--accent2` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | `--i` | — | 11／1 |
| `costume-g-blackdog` 小黑 | 1.0.0 | `--accent` `--bg` `--line` `--muted` `--ok` `--soft` `--surface` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-moon` `--th-rim` | `--gr` `--gy` `--i` | — | 18／1 |
| `costume-h-teapot` 小壺 | 1.0.0 | `--accent2` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | — | — | 13／0 |
| `costume-i-marquee` 小跑 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mi` | — | `--th-bulb` `--th-panel` | `--i` | — | 21／2 |
| `costume-j-tether` 牽牽 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | `--i` | — | 26／5 |
| `costume-k-pulse` 怦怦 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | `--i` | — | 15／2 |
| `costume-l-blocks` 格格 | 1.0.1 | `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | `--i` `--n` | — | 14／2 |
| `costume-m-bell` 叮叮 | 1.0.0 | `--bg` `--line` `--muted` `--ok` `--soft` `--surface` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-m3` | `--i` | — | 37／5 |
| `costume-n-gecko` 小守 | 1.0.0 | `--bad` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mi` | — | `--th-mp` | `--i` | — | 35／2 |
| `costume-o-puppy` 來福 | 1.0.0 | `--accent` `--accent2` `--bg` `--line` `--muted` `--ok` `--soft` `--surface` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-m3` | `--i` | — | 51／5 |
| `costume-p-lock` 小鎖 | 1.0.0 | `--bg` `--line` `--muted` `--ok` `--surface` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | — | `--i` | — | 7／0 |
| `costume-q-sticky` 貼貼 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-m3` | — | — | 14／1 |
| `costume-r-bento` 飽飽 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-fg` `--th-fr` `--th-fy` `--th-m3` `--th-wood` | `--i` `--x` | — | 15／1 |
| `costume-s-pangolin` 捲捲 | 1.0.0 | `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-mc` | `--i` | — | 9／0 |
| `costume-t-magpie` 啾啾 | 1.0.0 | `--bg` `--line` `--muted` `--ok` `--soft` `--surface` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-eye` `--th-leaf` `--th-tip` `--th-twig` `--th-wing` | `--i` | — | 8／0 |
| `costume-u-mountain` 靠山 | 1.0.0 | `--accent` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-cloud` `--th-cloudl` `--th-snow` `--th-storm` `--th-sun` | `--i` | — | 7／0 |
| `costume-v-kintsugi` 維維 | 1.0.0 | `--accent` `--bg` `--muted` `--ok` `--soft` `--surface` `--text` `--warn` | `--m1` `--m2` `--mi` | — | `--th-core` `--th-gem` `--th-gold` `--th-mside` `--th-mtop` | `--a1` `--a2` `--a3` `--ap` `--at` `--dx` `--dy` `--f0` `--fr` `--fx` `--fy` `--i` `--ir` `--ix` `--iy` `--o1` `--o2` `--o3` `--r` `--r0` `--sx` `--sy` `--t0` `--to` `--v1` `--v2` `--v3` | — | 13／3 |
| `costume-w-pixel` 嗶嗶 | 1.0.1 | `--bg` `--muted` `--ok` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-lcd` `--th-lcdink` `--th-mh` | — | — | 4／2 |
| `costume-x-circle` 圈圈 | 1.0.0 | `--bg` `--muted` `--ok` `--text` `--warn` | `--m1` `--m2` `--mi` | — | — | `--i` | — | 6／0 |
| `costume-y-clay` 捏捏 | 1.0.0 | `--accent` `--bg` `--muted` `--ok` `--soft` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-acd` `--th-mh` `--th-pe` | `--i` | — | 56／10 |
| `costume-z-cat` 呼嚕 | 1.0.0 | `--accent` `--bg` `--muted` `--ok` `--text` `--warn` | `--m1` `--m2` `--mb` `--mi` | — | `--th-mk` `--th-pe` `--th-sun` `--th-zo` `--th-zw` | `--i` | — | 20／1 |

## 寫死的顏色（逐套）

同一個地方、同一個顏色只列一次（後面是出現幾次）。★＝吉祥物或 App 圖示（多半是刻意的配色）。

### `costume-a-bubble` 方方

- ★ `appicon` fill：`#0a8a7c`
- ★ `appicon` fill：`#fff` ×2
- ★ `appicon` stroke：`#fff`
- ★ `mascot.happy` stroke：`#fff`
- ★ `mascot.idle` stroke：`#fff`
- ★ `mascot.think` stroke：`#fff`
- ★ `mascot.warn` stroke：`#fff`
- `opening` stroke：`#fff`

### `costume-b-lantern` 提燈

- ★ `appicon` fill：`#1f1d47`
- ★ `appicon` fill：`#3b2200` ×2
- ★ `appicon` fill：`#f3ead7` ×2
- ★ `appicon` fill：`#ffb84a` ×2
- ★ `appicon` stroke：`#4a4790`
- `opening` fill：`rgb(7,6,26)`
- `theme.css` [data-slot] .op-B .dim > .v > .bg {fill}：`rgb(7,6,26)`

### `costume-c-stamp` 小印

- ★ `appicon` fill：`#c8321f`
- ★ `appicon` fill：`#fff7f0` ×2
- ★ `appicon` stroke：`#fff7f0` ×2

### `costume-d-snail` 小殼

- ★ `appicon` fill：`#3d7a4f`
- ★ `appicon` stroke：`#fff`
- ★ `mascot.happy` stroke：`#fff`
- ★ `mascot.idle` stroke：`#fff`
- ★ `mascot.think` stroke：`#fff`
- ★ `mascot.warn` stroke：`#fff`
- `opening` stroke：`#fff`

### `costume-e-cursor` 游標

- ★ `appicon` fill：`#111114` ×3
- ★ `appicon` fill：`#9a87ff`
- ★ `appicon` fill：`#ededf2`
- ★ `appicon` stroke：`#3a3a46`

### `costume-f-mochi` 糰糰

- ★ `appicon` fill：`#3a2230` ×2
- ★ `appicon` fill：`#ff5d73` ×2
- ★ `appicon` fill：`#ff9a85`
- ★ `appicon` stroke：`#3a2230`
- ★ `appicon` stroke：`#cf4530`
- ★ `mascot.happy` stroke：`#fff`
- ★ `mascot.idle` stroke：`#fff`
- ★ `mascot.think` stroke：`#fff`
- ★ `mascot.warn` stroke：`#fff`
- `opening` stroke：`#fff`

### `costume-g-blackdog` 小黑

- ★ `appicon` fill：`#17150f` ×4
- ★ `appicon` fill：`#f0a92e` ×2
- ★ `appicon` fill：`#ff7d95` ×3
- ★ `appicon` fill：`#ffcf33`
- ★ `appicon` fill：`#fff`
- ★ `appicon` fill：`#fffaf0` ×3
- ★ `appicon` stroke：`#17150f`
- ★ `mascot.happy` fill：`#fff`
- ★ `mascot.idle` fill：`#fff`
- ★ `mascot.think` fill：`#fff`
- `opening` fill：`#fff`

### `costume-h-teapot` 小壺

- ★ `appicon` fill：`#1f44b5` ×3
- ★ `appicon` fill：`#f08d8d` ×2
- ★ `appicon` fill：`#fff` ×4
- ★ `appicon` stroke：`#1f44b5` ×2
- ★ `appicon` stroke：`#fff` ×2

### `costume-i-marquee` 小跑

- ★ `appicon` fill：`#1a0f24`
- ★ `appicon` fill：`#e3207f`
- ★ `appicon` fill：`#ffe45c`
- ★ `appicon` fill：`#ffe9a8` ×14
- ★ `mascot.happy` color：`#6ff0a8`
- ★ `mascot.happy` fill：`#6ff0a8`
- ★ `mascot.warn` color：`#ff8f3d`
- ★ `mascot.warn` fill：`#ff8f3d`
- `opening` fill：`rgb(227,32,127)`
- `theme.css` [data-slot][data-slot] .st-warn .ibulb {fill}：`rgb(255,143,61)`

### `costume-j-tether` 牽牽

- ★ `appicon` fill：`#3560f0`
- ★ `appicon` fill：`#3a2700` ×2
- ★ `appicon` fill：`#ffb92e`
- ★ `appicon` fill：`#fff` ×2
- ★ `appicon` stroke：`#3560f0`
- ★ `appicon` stroke：`#fff`
- ★ `mascot.happy` stroke：`#3a2700` ×2
- ★ `mascot.happy` stroke：`#fff` ×2
- ★ `mascot.idle` fill：`#3a2700` ×2
- ★ `mascot.idle` stroke：`#3a2700`
- ★ `mascot.idle` stroke：`#fff` ×2
- ★ `mascot.think` fill：`#3a2700` ×2
- ★ `mascot.think` stroke：`#fff` ×2
- ★ `mascot.warn` fill：`#3a2700` ×3
- ★ `mascot.warn` stroke：`#fff` ×2
- `opening` fill：`#3a2700` ×2
- `opening` stroke：`#3a2700`
- `opening` stroke：`#fff` ×2

### `costume-k-pulse` 怦怦

- ★ `appicon` fill：`#7a0c45` ×2
- ★ `appicon` fill：`#d4237f`
- ★ `appicon` fill：`#fff`
- ★ `appicon` stroke：`#7a0c45`
- ★ `appicon` stroke：`#fff` ×2
- ★ `mascot.happy` stroke：`#fff` ×2
- ★ `mascot.idle` stroke：`#fff` ×2
- ★ `mascot.think` stroke：`#fff` ×2
- ★ `mascot.warn` stroke：`#fff` ×2
- `opening` stroke：`#fff` ×2

### `costume-l-blocks` 格格

- ★ `appicon` fill：`#171b10` ×2
- ★ `appicon` fill：`#3a4520`
- ★ `appicon` fill：`#8fb81c`
- ★ `appicon` fill：`#c8f53c`
- ★ `appicon` fill：`#ffffff`
- ★ `mascot.happy` fill：`#1a2400`
- ★ `mascot.happy` fill：`#fff`
- ★ `mascot.idle` fill：`#1a2400`
- ★ `mascot.idle` fill：`#fff`
- ★ `mascot.think` fill：`#1a2400`
- ★ `mascot.think` fill：`#fff`
- ★ `mascot.warn` fill：`#1a2400`
- ★ `mascot.warn` fill：`#fff`
- `opening` fill：`#1a2400`
- `opening` fill：`#fff`

### `costume-m-bell` 叮叮

- ★ `appicon` fill：`#152a66`
- ★ `appicon` fill：`#2e5fe8` ×2
- ★ `appicon` fill：`#f2b430` ×6
- ★ `appicon` fill：`#fff` ×5
- ★ `appicon` stroke：`#fff` ×3
- ★ `mascot.happy` fill：`#fff`
- ★ `mascot.happy` stroke：`#fff` ×4
- ★ `mascot.idle` fill：`#fff`
- ★ `mascot.idle` stroke：`#fff` ×4
- ★ `mascot.think` fill：`#fff`
- ★ `mascot.think` stroke：`#fff` ×4
- ★ `mascot.warn` fill：`#fff`
- ★ `mascot.warn` stroke：`#fff` ×4
- `opening` fill：`#fff`
- `opening` stroke：`#fff` ×4

### `costume-n-gecko` 小守

- ★ `appicon` fill：`#171d0b` ×10
- ★ `appicon` fill：`#465a22` ×5
- ★ `appicon` fill：`#c6f135` ×6
- ★ `appicon` fill：`#fff` ×2
- ★ `appicon` stroke：`#171d0b` ×6
- ★ `mascot.idle` fill：`#fff` ×2
- ★ `mascot.think` fill：`#fff` ×2
- ★ `mascot.warn` fill：`#fff` ×2
- `opening` fill：`#fff` ×2

### `costume-o-puppy` 來福

- ★ `appicon` fill：`#3a2212` ×6
- ★ `appicon` fill：`#4a2b18` ×2
- ★ `appicon` fill：`#c98445` ×4
- ★ `appicon` fill：`#e3a55c` ×2
- ★ `appicon` fill：`#f6c443`
- ★ `appicon` fill：`#ff8296` ×2
- ★ `appicon` fill：`#fff` ×2
- ★ `appicon` fill：`#fff1dc` ×4
- ★ `appicon` stroke：`#3a2212`
- ★ `appicon` stroke：`#7a4d2e`
- ★ `appicon` stroke：`#c98445` ×4
- ★ `appicon` stroke：`#e2452f`
- ★ `mascot.happy` fill：`#f6c443`
- ★ `mascot.happy` fill：`#fff`
- ★ `mascot.happy` stroke：`#e2452f`
- ★ `mascot.idle` fill：`#f6c443`
- ★ `mascot.idle` fill：`#fff` ×3
- ★ `mascot.idle` stroke：`#e2452f`
- ★ `mascot.think` fill：`#f6c443`
- ★ `mascot.think` fill：`#fff` ×3
- ★ `mascot.think` stroke：`#e2452f`
- ★ `mascot.warn` fill：`#f6c443`
- ★ `mascot.warn` fill：`#fff` ×3
- ★ `mascot.warn` fill：`#fffdf7`
- ★ `mascot.warn` stroke：`#b9ac9c` ×2
- ★ `mascot.warn` stroke：`#e2452f`
- `opening` fill：`#f6c443`
- `opening` fill：`#fff` ×3
- `opening` stroke：`#e2452f`

### `costume-p-lock` 小鎖

- ★ `appicon` fill：`#1d1d1f`
- ★ `appicon` fill：`#f7f7f9`
- ★ `appicon` fill：`#fff` ×2
- ★ `appicon` stroke：`#8e8e93`
- ★ `appicon` stroke：`#d2d2d7`
- ★ `appicon` stroke：`#fff`

### `costume-q-sticky` 貼貼

- ★ `appicon` fill：`#26323a` ×2
- ★ `appicon` fill：`#52c2d2`
- ★ `appicon` fill：`#e3b22e`
- ★ `appicon` fill：`#ff8f8f` ×2
- ★ `appicon` fill：`#ffdf6b`
- ★ `appicon` stroke：`#26323a`
- ★ `mascot.happy` fill：`#000`
- ★ `mascot.happy` stroke：`#1c7a4c`
- ★ `mascot.idle` fill：`#000`
- ★ `mascot.think` fill：`#000`
- ★ `mascot.warn` fill：`#000`
- ★ `mascot.warn` stroke：`#9a6408`
- `opening` fill：`#000`

### `costume-r-bento` 飽飽

- ★ `appicon` fill：`#2f2140` ×2
- ★ `appicon` fill：`#5f4199` ×3
- ★ `appicon` fill：`#a98bd9`
- ★ `appicon` fill：`#ff7f9c` ×2
- ★ `appicon` stroke：`#2f2140`
- ★ `appicon` stroke：`#c98b4e`
- ★ `appicon` stroke：`#fff4dc`
- ★ `mascot.happy` stroke：`#fff`
- ★ `mascot.idle` stroke：`#fff`
- ★ `mascot.think` stroke：`#fff`
- ★ `mascot.warn` stroke：`#fff`
- `opening` stroke：`#fff`

### `costume-s-pangolin` 捲捲

- ★ `appicon` fill：`#3a2648` ×2
- ★ `appicon` fill：`#4a2f66`
- ★ `appicon` fill：`#bfa6dc`
- ★ `appicon` fill：`#ff8f8f` ×2
- ★ `appicon` fill：`#fff3e0`
- ★ `appicon` stroke：`#3a2648`
- ★ `appicon` stroke：`#6a4c8c`

### `costume-t-magpie` 啾啾

- ★ `appicon` fill：`#151a26` ×3
- ★ `appicon` fill：`#1b86d8`
- ★ `appicon` fill：`#ff5238`
- ★ `appicon` fill：`#ffd84a` ×2
- ★ `appicon` stroke：`#ff5238`

### `costume-u-mountain` 靠山

- ★ `appicon` fill：`#443f78`
- ★ `appicon` fill：`#f47b2a`
- ★ `appicon` fill：`#ffe08a`
- ★ `appicon` fill：`#fff6e6` ×2
- ★ `appicon` fill：`#ffffff`
- ★ `appicon` stroke：`#fff6e6`

### `costume-v-kintsugi` 維維

- ★ `appicon` fill：`#1b2547`
- ★ `appicon` fill：`#dcbb74`
- ★ `appicon` fill：`#fff6dc`
- ★ `appicon` stroke：`#c9a45c` ×2
- ★ `mascot.happy` fill：`#fff` ×2
- ★ `mascot.idle` fill：`#fff` ×2
- ★ `mascot.think` fill：`#fff` ×2
- ★ `mascot.warn` fill：`#fff` ×2
- `opening` fill：`#fff` ×2
- `opening` fill：`rgb(245,240,228)`

### `costume-w-pixel` 嗶嗶

- ★ `appicon` fill：`#0f380f`
- ★ `appicon` fill：`#7f9f0c`
- ★ `appicon` fill：`#9bbc0f`
- ★ `appicon` fill：`#d6336c`
- `opening` fill：`rgb(0,0,0)` ×2

### `costume-x-circle` 圈圈

- ★ `appicon` fill：`#0071e3`
- ★ `appicon` fill：`#1d1d1f` ×2
- ★ `appicon` fill：`#ffffff`
- ★ `appicon` stroke：`#1d1d1f`
- ★ `appicon` stroke：`#d2d2d7`

### `costume-y-clay` 捏捏

- ★ `appicon` fill：`#33254f` ×2
- ★ `appicon` fill：`#ff8fb0` ×2
- ★ `appicon` fill：`#fff` ×3
- ★ `appicon` stop-color：`#3a2596` ×2
- ★ `appicon` stop-color：`#6f56d6`
- ★ `appicon` stop-color：`#b3a2ff`
- ★ `appicon` stop-color：`#e0d7ff`
- ★ `appicon` stop-color：`#fff` ×2
- ★ `appicon` stroke：`#33254f`
- ★ `mascot.happy` fill：`#fff` ×2
- ★ `mascot.happy` flood-color：`#2a1a66`
- ★ `mascot.happy` flood-color：`#ffffff`
- ★ `mascot.happy` stop-color：`#1b1035` ×2
- ★ `mascot.happy` stop-color：`#fff` ×2
- ★ `mascot.happy` stroke：`#fff`
- ★ `mascot.idle` fill：`#fff` ×3
- ★ `mascot.idle` flood-color：`#2a1a66`
- ★ `mascot.idle` flood-color：`#ffffff`
- ★ `mascot.idle` stop-color：`#1b1035` ×2
- ★ `mascot.idle` stop-color：`#fff` ×2
- ★ `mascot.idle` stroke：`#fff`
- ★ `mascot.think` fill：`#fff` ×3
- ★ `mascot.think` flood-color：`#2a1a66`
- ★ `mascot.think` flood-color：`#ffffff`
- ★ `mascot.think` stop-color：`#1b1035` ×2
- ★ `mascot.think` stop-color：`#fff` ×2
- ★ `mascot.think` stroke：`#fff`
- ★ `mascot.warn` fill：`#fff` ×3
- ★ `mascot.warn` flood-color：`#2a1a66`
- ★ `mascot.warn` flood-color：`#ffffff`
- ★ `mascot.warn` stop-color：`#1b1035` ×2
- ★ `mascot.warn` stop-color：`#fff` ×2
- ★ `mascot.warn` stroke：`#7a3d00`
- ★ `mascot.warn` stroke：`#fff` ×2
- `opening` fill：`#fff` ×3
- `opening` flood-color：`#2a1a66`
- `opening` flood-color：`#ffffff`
- `opening` stop-color：`#1b1035` ×2
- `opening` stop-color：`#fff` ×2
- `opening` stroke：`#fff`

### `costume-z-cat` 呼嚕

- ★ `appicon` fill：`#2e2a35` ×2
- ★ `appicon` fill：`#3f5d91`
- ★ `appicon` fill：`#ef8791`
- ★ `appicon` fill：`#f2939b` ×3
- ★ `appicon` fill：`#f2a25a` ×2
- ★ `appicon` fill：`#ffd0c4`
- ★ `appicon` fill：`#fff4e3`
- ★ `appicon` stroke：`#2e2a35` ×2
- ★ `appicon` stroke：`#ef8791`
- ★ `mascot.idle` fill：`#fff`
- ★ `mascot.think` fill：`#fff` ×2
- ★ `mascot.warn` fill：`#fff` ×2
- ★ `mascot.warn` stroke：`#6b3500`
- `opening` fill：`#fff`
