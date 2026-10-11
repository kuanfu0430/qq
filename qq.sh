# qq 指令集 - shell 入口
#
# 以 source 方式載入（見 ~/.zshrc 或 ~/.bashrc），定義 qq() 函式。bash、zsh 都能用。
# 之所以必須用 shell function 而非獨立執行檔，是因為子行程無法改變
# 父 shell 的所在目錄；只有函式能在「當前 shell」中執行 cd，這樣
# `qq find -d mnt && ls` 才會在切換後才執行 ls。

# QQ_HOME：先看環境變數，再看本檔所在目錄（bash / zsh 都適用），最後才退回 ~/qq。
# 這樣 sudo -s 之後 HOME 變了也找得到程式。
# 專案：https://github.com/kuanfu0430/qq  （升級：重跑安裝指令）
if [ -z "${QQ_HOME:-}" ]; then
    if [ -n "${ZSH_VERSION:-}" ]; then
        QQ_HOME="${${(%):-%x}:A:h}"
    elif [ -n "${BASH_VERSION:-}" ]; then
        QQ_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    fi
    if [ ! -d "${QQ_HOME:-}/bin" ]; then
        if [ -d /usr/local/share/qq/bin ]; then
            QQ_HOME=/usr/local/share/qq
        else
            QQ_HOME="$HOME/qq"
        fi
    fi
fi

qq_help() {
    cat <<'EOF'
qq 指令集

用法：
  qq <子指令> [參數...]

子指令：
  find    依名稱搜尋資料夾／檔案，互動選擇後切換目錄
  pick    依名稱搜尋，選完把完整路徑印出來（人留在原地）
  grep    依檔案內容搜尋，互動選擇後切換到該檔所在資料夾
  help    顯示本說明

索引：
  qq-updatedb             立即重建 find / pick 用的索引（平常每天自動更新；Linux 有 plocate 時不需要）

範例：
  qq find -d mnt          搜尋名稱為 mnt 的資料夾並切換過去
  qq find -f config.yaml  搜尋檔案並切換到它所在資料夾
  qq find --fresh -d mnt  不使用索引，即時掃描硬碟
  qq find -d mnt && ls    切換過去後直接執行 ls
  vim "$(qq pick -f hosts)"
  cp "$(qq pick -f notes.md)" ./
  qq grep TODO            找內容含 TODO 的檔，選完走進它所在資料夾

（各子指令的詳細說明：qq find --help、qq pick --help、qq grep --help）
EOF
}

# 共用：把 --help / -h 攔下來直接代印，避免 $(...) 把說明文字當成路徑
_qq_wants_help() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            --) return 1 ;;
            -h|--help) return 0 ;;
        esac
    done
    return 1
}

_qq_run_bin() {
    command "${QQ_PYTHON:-python3}" "$QQ_HOME/bin/$1" "${@:2}"
}

_qq_cd_to_target() {
    local target="$1"
    if [ -d "$target" ]; then
        builtin cd -- "$target" || return 1
    else
        builtin cd -- "$(dirname -- "$target")" || return 1
    fi
    printf '→ %s\n' "$target" >&2
}

qq() {
    local sub="${1:-}"

    case "$sub" in
        find)
            shift
            if _qq_wants_help "$@"; then
                _qq_run_bin qq-find --help
                return $?
            fi
            local target rc
            target="$(_qq_run_bin qq-find "$@")"
            rc=$?
            [ "$rc" -ne 0 ] && return "$rc"
            [ -z "$target" ] && return 1
            _qq_cd_to_target "$target"
            ;;
        pick)
            shift
            if _qq_wants_help "$@"; then
                _qq_run_bin qq-pick --help
                return $?
            fi
            # 人不動，只把完整路徑印到 stdout 給 $(...) 使用
            _qq_run_bin qq-pick "$@"
            ;;
        grep)
            shift
            if _qq_wants_help "$@"; then
                _qq_run_bin qq-grep --help
                return $?
            fi
            local target rc
            target="$(_qq_run_bin qq-grep "$@")"
            rc=$?
            [ "$rc" -ne 0 ] && return "$rc"
            [ -z "$target" ] && return 1
            _qq_cd_to_target "$target"
            ;;
        ""|help|-h|--help)
            qq_help
            ;;
        *)
            printf 'qq: 未知的子指令「%s」\n\n' "$sub" >&2
            qq_help >&2
            return 2
            ;;
    esac
}

# ---- 補全 ----------------------------------------------------------------
if [ -n "${BASH_VERSION:-}" ]; then
_qq_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W "find pick grep help" -- "$cur"))
        return
    fi

    case "${COMP_WORDS[1]}" in
        find|pick)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "-d -f --fresh --help" -- "$cur"))
            fi
            ;;
        grep)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=($(compgen -W "--regex --help" -- "$cur"))
            fi
            ;;
    esac
}
complete -F _qq_complete qq 2>/dev/null || true
fi

if [ -n "${ZSH_VERSION:-}" ]; then
_qq_zsh_complete() {
    if [ "$CURRENT" -eq 2 ]; then
        compadd -- find pick grep help
        return
    fi
    case "${words[2]}" in
        find|pick)
            if [[ "${words[CURRENT]}" == -* ]]; then
                compadd -- -d -f --fresh --help
            fi
            ;;
        grep)
            if [[ "${words[CURRENT]}" == -* ]]; then
                compadd -- --regex --help
            else
                _files -/
            fi
            ;;
    esac
}
# compinit 要先載入（~/.zshrc 前段已經跑過）；沒有就略過，不影響 qq 本身
if whence compdef >/dev/null 2>&1; then
    compdef _qq_zsh_complete qq
fi
fi
