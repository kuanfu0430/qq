#!/bin/sh
# qq 安裝程式（Linux / macOS）
#   curl -fsSL https://raw.githubusercontent.com/kuanfu0430/qq/main/install.sh | sh
#   … | sh -s -- --os linux|mac   指定版本（預設自動判斷）
#   … | sh -s -- --uninstall      移除
#   QQ_VERSION=v1.0.0 … | sh       安裝指定版本（git tag / branch，預設 main）
# 不需要 sudo；檔案只放在家目錄。重跑 = 升級。
set -eu

REPO="kuanfu0430/qq"
REF="${QQ_VERSION:-main}"
QQ_DIR="${QQ_DIR:-$HOME/qq}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/qq"
LBIN="$HOME/.local/bin"
MARK_BEGIN="# >>> qq >>>"
MARK_END="# <<< qq <<<"
MAC_LABEL="com.kuanfu0430.qq-updatedb"
MAC_PLIST="$HOME/Library/LaunchAgents/$MAC_LABEL.plist"

OS=""
ACTION="install"
while [ $# -gt 0 ]; do
    case "$1" in
        --os) OS="${2:-}"; shift 2 ;;
        --os=*) OS="${1#--os=}"; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        -h|--help) sed -n '2,7p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "qq-install: 未知參數 $1" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1mqq-install:\033[0m %s\n' "$*"; }
warn() { printf '\033[33mqq-install: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[31mqq-install: %s\033[0m\n' "$*" >&2; exit 1; }

if [ -z "$OS" ]; then
    case "$(uname -s)" in
        Darwin) OS=mac ;;
        Linux) OS=linux ;;
        *) die "不支援的系統 $(uname -s)（Windows 請用 install.ps1）" ;;
    esac
fi
case "$OS" in mac|linux) ;; *) die "--os 只能是 linux 或 mac" ;; esac

rc_files() {
    # 登入 shell 的 rc 一定寫；另一個 rc 檔若已存在也寫（bash／zsh 都能用 qq）
    sh_name="$(basename "${SHELL:-sh}")"
    case "$sh_name" in
        zsh) echo "$HOME/.zshrc" ;;
        bash) echo "$HOME/.bashrc" ;;
    esac
    for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
        [ -f "$f" ] && echo "$f"
    done
    [ "$OS" = mac ] && [ "$sh_name" != bash ] && [ "$sh_name" != zsh ] && echo "$HOME/.zshrc"
    true
}

strip_block() { # 從 rc 檔移除 qq 區塊（新舊格式都處理）
    f="$1"
    [ -f "$f" ] || return 0
    tmp="$f.qqtmp.$$"
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
        $0 == b {skip=1; next}
        $0 == e {skip=0; next}
        skip {next}
        /^# qq 指令集（自製）/ {old=1; next}
        old && /^if \[ -f "\$HOME\/qq\/qq.sh" \]; then$/ {next}
        old && /^    \. "\$HOME\/qq\/qq.sh"$/ {next}
        old && /^fi$/ {old=0; next}
        {old=0; print}
    ' "$f" > "$tmp" && cat "$tmp" > "$f"
    rm -f "$tmp"
}

remove_schedule() {
    if [ "$OS" = mac ]; then
        for label in "$MAC_LABEL" com.neil.qq-updatedb; do
            launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
            rm -f "$HOME/Library/LaunchAgents/$label.plist"
        done
    else
        if command -v crontab >/dev/null 2>&1 && crontab -l >/dev/null 2>&1; then
            crontab -l 2>/dev/null | grep -v '# qq-updatedb$' | crontab - || true
        fi
        if command -v systemctl >/dev/null 2>&1; then
            systemctl --user disable --now qq-updatedb.timer >/dev/null 2>&1 || true
            rm -f "$HOME/.config/systemd/user/qq-updatedb.service" "$HOME/.config/systemd/user/qq-updatedb.timer"
            systemctl --user daemon-reload >/dev/null 2>&1 || true
        fi
    fi
}

if [ "$ACTION" = uninstall ]; then
    for f in $(rc_files | sort -u); do strip_block "$f"; done
    remove_schedule
    for n in qq qq-updatedb; do
        [ -L "$LBIN/$n" ] && rm -f "$LBIN/$n"
    done
    rm -rf "$QQ_DIR" "$CACHE"
    say "已移除。開新終端機後 qq 就不在了。"
    exit 0
fi

# ---------------------------------------------------------------- 檢查依賴
PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "需要 python3（Debian/Ubuntu: sudo apt install python3；macOS: xcode-select --install 或 brew install python）"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || die "python3 版本太舊（需要 3.8 以上）"
"$PY" -c 'import curses' 2>/dev/null || warn "python3 沒有 curses 模組，互動選單無法使用（Linux 通常裝 python3 套件即有）"
command -v tar >/dev/null 2>&1 || die "需要 tar"

# ---------------------------------------------------------------- 下載
TMPD="$(mktemp -d 2>/dev/null || mktemp -d -t qq)"
trap 'rm -rf "$TMPD"' EXIT INT TERM
URL="https://codeload.github.com/$REPO/tar.gz/$REF"
say "下載 $REPO@$REF"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" -o "$TMPD/qq.tgz" || die "下載失敗：$URL"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TMPD/qq.tgz" "$URL" || die "下載失敗：$URL"
else
    die "需要 curl 或 wget"
fi
tar xzf "$TMPD/qq.tgz" -C "$TMPD"
SRC="$(find "$TMPD" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -f "$SRC/qq.sh" ] && [ -f "$SRC/bin/qq_lib.py" ] || die "下載內容不完整"

# ---------------------------------------------------------------- 安裝檔案
if [ -d "$QQ_DIR" ] && [ ! -f "$QQ_DIR/VERSION" ]; then
    BAK="$QQ_DIR.bak-$(date +%Y%m%d%H%M%S)"
    mv "$QQ_DIR" "$BAK"
    say "舊版 qq 移到 $BAK"
fi
mkdir -p "$QQ_DIR"
rm -rf "$QQ_DIR/bin"
cp -R "$SRC/bin" "$QQ_DIR/bin"
cp "$SRC/qq.sh" "$QQ_DIR/qq.sh"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$QQ_DIR/README.md"
chmod +x "$QQ_DIR/bin/"*
rm -f "$QQ_DIR/bin/"*.ps1 2>/dev/null || true
{
    echo "ref=$REF"
    echo "os=$OS"
    echo "installed=$(date '+%F %T')"
} > "$QQ_DIR/VERSION"

# PATH 後備入口
mkdir -p "$LBIN"
ln -sf "$QQ_DIR/bin/qq-cli" "$LBIN/qq"
ln -sf "$QQ_DIR/bin/qq-updatedb" "$LBIN/qq-updatedb"

# rc 檔：qq() 函式
for f in $(rc_files | sort -u); do
    strip_block "$f"
    {
        echo ""
        echo "$MARK_BEGIN"
        echo "case \":\$PATH:\" in *\":\$HOME/.local/bin:\"*) ;; *) export PATH=\"\$HOME/.local/bin:\$PATH\" ;; esac"
        echo "[ -f \"$QQ_DIR/qq.sh\" ] && . \"$QQ_DIR/qq.sh\""
        echo "$MARK_END"
    } >> "$f"
    say "已寫入 $f"
done

# ---------------------------------------------------------------- 索引
mkdir -p "$CACHE"
remove_schedule
NEED_BUILD=1
if [ "$OS" = mac ]; then
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$MAC_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$MAC_LABEL</string>
	<key>ProgramArguments</key>
	<array><string>/bin/sh</string><string>-c</string><string>exec python3 "$QQ_DIR/bin/qq-updatedb" --quiet</string></array>
	<key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
	<key>StartCalendarInterval</key><dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
	<key>ProcessType</key><string>Background</string>
	<key>LowPriorityIO</key><true/>
	<key>Nice</key><integer>10</integer>
	<key>StandardOutPath</key><string>$CACHE/updatedb.log</string>
	<key>StandardErrorPath</key><string>$CACHE/updatedb.log</string>
</dict>
</plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$MAC_PLIST" 2>/dev/null || warn "LaunchAgent 載入失敗（之後可手動 qq-updatedb）"
    say "排程：LaunchAgent $MAC_LABEL，每天 12:30 重建 locate 索引"
elif command -v plocate >/dev/null 2>&1; then
    NEED_BUILD=0
    say "索引：使用系統 plocate（由系統自動更新）"
else
    JOB="30 12 * * * \"$PY\" \"$QQ_DIR/bin/qq-updatedb\" --quiet >> \"$CACHE/updatedb.log\" 2>&1 # qq-updatedb"
    if command -v crontab >/dev/null 2>&1; then
        { crontab -l 2>/dev/null | grep -v '# qq-updatedb$' || true; echo "$JOB"; } | crontab -
        say "排程：crontab 每天 12:30 重建 qq 索引"
    elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
        U="$HOME/.config/systemd/user"; mkdir -p "$U"
        printf '[Unit]\nDescription=qq-updatedb\n[Service]\nType=oneshot\nNice=10\nExecStart=%s %s --quiet\n' "$PY" "$QQ_DIR/bin/qq-updatedb" > "$U/qq-updatedb.service"
        printf '[Unit]\nDescription=qq-updatedb daily\n[Timer]\nOnCalendar=*-*-* 12:30:00\nPersistent=true\n[Install]\nWantedBy=timers.target\n' > "$U/qq-updatedb.timer"
        systemctl --user daemon-reload && systemctl --user enable --now qq-updatedb.timer >/dev/null 2>&1 || true
        say "排程：systemd --user timer 每天 12:30 重建 qq 索引"
    else
        warn "找不到 crontab / systemd，索引不會自動更新；需要時手動 qq-updatedb"
    fi
fi
if [ "$NEED_BUILD" = 1 ]; then
    nohup "$PY" "$QQ_DIR/bin/qq-updatedb" > "$CACHE/updatedb.log" 2>&1 &
    say "首次建索引已在背景執行（約 1～5 分鐘，紀錄 $CACHE/updatedb.log）；期間可先用 --fresh"
fi

# ---------------------------------------------------------------- 結果
command -v fd >/dev/null 2>&1 || command -v fdfind >/dev/null 2>&1 || \
    warn "選用：安裝 fd 可加快 --fresh（apt: fd-find／brew: fd）"
command -v rg >/dev/null 2>&1 || \
    warn "選用：安裝 ripgrep 可加快 qq grep（apt: ripgrep／brew: ripgrep）"
say "完成 ✔  開新終端機後即可使用：qq help"
