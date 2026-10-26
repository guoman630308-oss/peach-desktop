#!/usr/bin/env bash
# macOS/Linux 的唯一测试入口，与 Windows 的 scripts/test.ps1 同一套契约：
# 优先当前 worktree 的 .venv，缺失时定位主目录环境；强制加载当前树的 src，
# 并核对 peach.__file__ 确实来自这个 worktree。worktree 不复制 .venv。
set -euo pipefail

SCOPE="${1:-auto}"
case "$SCOPE" in
    full|auto|follow|catalog|media|sync|metadata|tooling|web|checks|core|packaging) ;;
    *)
        echo "未知测试域：$SCOPE（可选 full、auto、follow、catalog、media、sync、metadata、tooling、web）" >&2
        exit 2
        ;;
esac
# 本机默认并行：没显式给 `--jobs` 就传 auto，运行器按核数定并发；CI 分片模式下它被忽略。
# `${1+$*}` 是 bash 3.2 在 `set -u` 下读全部参数的写法，没有参数时安全地展开成空。
JOBS=(--jobs auto)
case " ${1+$*} " in
    *" --jobs "*) JOBS=() ;;
esac
EXTRA=("${@:2}")
# 展开必须写成 `${EXTRA[@]+...}`。macOS 自带的是 bash 3.2，`set -u` 下它把空数组的
# `"${EXTRA[@]}"` 当未绑定变量报错（bash 4.4 起才不报），于是不带额外参数直接跑
# `./scripts/test.sh` 会在最后一行崩掉。CI 每次都附带 `--fresh --base ...`，数组从不为空，
# 这条路径只有本机会走到。不要改成 `set +u`：其余变量的拼写错误就没人拦了。

WORKTREE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

GIT_COMMON_RAW="$(git -C "$WORKTREE_ROOT" rev-parse --git-common-dir)"
if [[ -z "$GIT_COMMON_RAW" ]]; then
    echo "无法定位 Peach 主工作树。" >&2
    exit 1
fi
if [[ "$GIT_COMMON_RAW" = /* ]]; then
    GIT_COMMON="$GIT_COMMON_RAW"
else
    GIT_COMMON="$WORKTREE_ROOT/$GIT_COMMON_RAW"
fi
GIT_COMMON="$(cd "$GIT_COMMON" && pwd)"
MAIN_ROOT="$(dirname "$GIT_COMMON")"

PYTHON="$MAIN_ROOT/.venv/bin/python"
if [[ -x "$WORKTREE_ROOT/.venv/bin/python" ]]; then
    PYTHON="$WORKTREE_ROOT/.venv/bin/python"
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "Peach 测试 venv 不存在：$PYTHON" >&2
    exit 1
fi

SOURCE_ROOT="$WORKTREE_ROOT/src"
export PYTHONPATH="$SOURCE_ROOT"
export PYTHONIOENCODING=utf-8

cd "$WORKTREE_ROOT"
"$PYTHON" -X utf8 scripts/test_environment.py "$WORKTREE_ROOT" "$MAIN_ROOT"
PYTHON="$WORKTREE_ROOT/.venv/bin/python"
LOADED_MODULE="$("$PYTHON" -c 'import peach; print(peach.__file__)')"
if [[ "$LOADED_MODULE" != "$SOURCE_ROOT/"* ]]; then
    echo "测试加载了错误源码：$LOADED_MODULE；预期位于 $SOURCE_ROOT" >&2
    exit 1
fi

echo "Peach source: $LOADED_MODULE"
exec "$PYTHON" scripts/test_runner.py --scope "$SCOPE" ${JOBS[@]+"${JOBS[@]}"} ${EXTRA[@]+"${EXTRA[@]}"}
