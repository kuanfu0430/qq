# qq

互動式找檔／找目錄並切換過去。Linux、macOS、Windows 共用同一份 Python 核心。

| 指令 | 作用 |
|---|---|
| `qq find [-d\|-f] [--fresh] <名稱>` | 依名稱找，選完 cd 過去（選到檔案就 cd 到它所在的資料夾） |
| `qq pick [-d\|-f] [--fresh] <名稱>` | 依名稱找，選完只輸出路徑，不 cd |
| `qq grep [--regex] <文字> [目錄]` | 依內容找，選完 cd 到該檔所在的資料夾 |
| `qq-updatedb` | 立即重建名稱索引（平常每天 12:30 自動更新） |

名稱要完全相符，不分大小寫。選單：↑↓/jk 移動、Enter 選、q/Esc 取消。

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
