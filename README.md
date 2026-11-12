# qq

互動式找檔／找目錄並切換過去。Linux、macOS、Windows 共用同一份 Python 核心。

| 指令 | 作用 |
|---|---|
| `qq find [-d\|-f] [--fresh] <名稱>` | 依名稱找，選完 cd 過去（選到檔案就 cd 到它所在的資料夾） |
| `qq pick [-d\|-f] [--fresh] <名稱>` | 依名稱找，選完只輸出路徑，不 cd |
| `qq grep [--regex] <文字> [目錄]` | 依內容找，選完 cd 到該檔所在的資料夾 |
| `qq cp [-f] [--fresh] <來源...> <目的地>` | 複製；目的地只打資料夾名稱時先搜尋，選定再複製 |
| `qq scp [-P 埠] [-i 金鑰] <來源...> <[使用者@]機器[:]>` | 傳到別台機器，自動放進對方的 `~/Downloads`（沒有就放家目錄） |
| `qq open <分鐘>`／`--status`／`--revoke` | 開放 mcp 的限時 sudo（例如 `qq open 60` 就是一小時，到期自動撤銷）；目前只在 Minis 上能用 |
| `qq-updatedb` | 立即重建名稱索引（平常每天 12:30 自動更新） |

find／pick 的名稱要完全相符，不分大小寫。選單：↑↓/jk 移動、Enter 選、q/Esc 取消。

### qq cp

```sh
qq cp ~/mnt/test.txt document     # 列出 document、Documents… 等資料夾，選一個後複製過去
qq cp a.txt b.txt projects        # 一次複製多個
qq cp -f ~/mnt/photos Downloads   # 資料夾整個複製；已存在就直接覆蓋
qq cp report.pdf ~/Desktop        # 目的地寫成路徑時不搜尋，跟一般 cp 一樣
```

- 目的地只有名稱時：同名資料夾排前面，接著是名稱開頭相符的（`document` 也會找到 `Documents`）；家目錄底下的優先；沒有寫入權限的資料夾不列出。
- 目的地已有同名檔案時會先問要不要覆蓋；加 `-f` 不問。
- 沒有互動終端機時，只有找到唯一一個資料夾才會複製。
- `--fresh` 即時掃描；設 `QQ_FRESH_ROOTS=~/a:~/b` 可限定掃描範圍（避開很慢的網路磁碟）。

### qq scp

```sh
qq scp ~/mnt/test.txt manager@mcp:    # 放到 mcp 的 ~/Downloads
qq scp ~/mnt/photos manager@civil     # 冒號可省略；資料夾自動整個傳
qq scp notes.md manager@mcp:/tmp      # 冒號後面有路徑就照寫的放
```

- 先用 sftp 看對方有沒有 `Downloads`，再用 scp 傳；兩步共用同一條連線，需要密碼只問一次（Windows 用戶端除外）。對方是 Windows 也可以。
- 機器可以是 MagicDNS 短名、IP，或 `~/.ssh/config` 的 Host。

## 安裝

**Linux / macOS**
```sh
curl -fsSL https://raw.githubusercontent.com/kuanfu0430/qq/main/install.sh | sh
```

**Windows（PowerShell）**
```powershell
irm https://raw.githubusercontent.com/kuanfu0430/qq/main/install.ps1 | iex
```

重跑同一條指令就是升級。裝完開新終端機即可使用。

| | Linux / macOS | Windows |
|---|---|---|
| 指定版本 | `curl … \| QQ_VERSION=v1.0.0 sh` | `$env:QQ_VERSION='v1.0.0'; irm … \| iex` |
| 強制選擇版本 | `curl … \| sh -s -- --os linux` 或 `--os mac` | — |
| 移除 | `curl … \| sh -s -- --uninstall` | `$env:QQ_UNINSTALL=1; irm … \| iex` |

## 需求

| | 必要 | 選用 |
|---|---|---|
| Linux | python3 3.8+（含 curses）、curl 或 wget、tar | plocate、fd、ripgrep |
| macOS | python3 3.8+ | fd、ripgrep（`brew install fd ripgrep`） |
| Windows | Python 3.8+（沒有時用 winget 自動裝）、PowerShell 5.1+；`windows-curses` 會自動裝 | fd、ripgrep（`winget install sharkdp.fd BurntSushi.ripgrep.MSVC`） |

都不需要 sudo 或系統管理員權限。

## 索引

| 系統 | 名稱索引 | 自動更新 |
|---|---|---|
| Linux | 有 plocate 就用 plocate；沒有的話，qq 自己建 `~/.cache/qq/index.gz` | 系統 plocate／crontab 或 systemd user timer |
| macOS | Spotlight ＋ 系統內建 locate（建到 `~/.cache/qq/locate.database`），兩者合併 | LaunchAgent `com.kuanfu0430.qq-updatedb` |
| Windows | qq 自己掃全部固定磁碟，建到 `%LOCALAPPDATA%\qq\index.gz` | 工作排程器 `qq-updatedb` |

剛建立的檔案還沒進索引時，加 `--fresh` 直接掃描。

## 安裝位置

- Linux／macOS：`~/qq/`，在 `~/.zshrc`／`~/.bashrc` 加入 `# >>> qq >>>` 區塊，並建立 `~/.local/bin/qq`（還沒載入函式時的備用入口）
- Windows：`%LOCALAPPDATA%\qq\app\`，在 PowerShell 5.1／7 的 `$PROFILE` 加入 `# >>> qq >>>` 區塊

### qq open

```sh
qq open 60         # 開放 mcp 完整 sudo 一小時（1～240 分鐘），到期自動撤銷
qq open --status   # 是否開放中、何時到期
qq open --revoke   # 提早撤銷
```

- 轉呼叫 `mcp-elevate.sh`（預設 `/var/minis/shared/mcp-elevate/mcp-elevate.sh`，可用 `QQ_MCP_ELEVATE` 指定）。
- 密碼取自環境變數 `MCP_SUDO_PASSWORD`，只經 stdin 交給 mcp 上的 `sudo -S`，不出現在指令列或 log。
- mcp 端執行的是 root 擁有的 `/usr/local/sbin/agent-elevate`；到期由 `agent-elevate-sweep.timer` 撤銷，重開機也會撤銷。
