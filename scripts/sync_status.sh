#!/bin/sh
# 开工前先看一眼：本地和另一台机器差了多少。
#
# 两台机器共用一个私有 GitHub 仓库，但**不自动推送**——自动推会把还没审的改动直接发出去，
# 也绕过「push 前先确认」这条规矩。所以同步是手动的，这个脚本只负责让「该 pull 了」这件事
# 一眼可见，不然很容易在旧代码上接着写。
#
#   sh scripts/sync_status.sh
set -eu

cd "$(dirname "$0")/.."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if ! git remote get-url origin >/dev/null 2>&1; then
    echo "没有配置 origin，代码同步未启用"
    exit 0
fi

echo "分支：${BRANCH}"
if ! git fetch --quiet origin "$BRANCH" 2>/dev/null; then
    echo "  取远端失败（没网或没权限），只报告本地状态"
else
    AHEAD="$(git rev-list --count "origin/${BRANCH}..${BRANCH}" 2>/dev/null || echo 0)"
    BEHIND="$(git rev-list --count "${BRANCH}..origin/${BRANCH}" 2>/dev/null || echo 0)"
    if [ "$BEHIND" -gt 0 ]; then
        echo "  ⚠ 落后远端 ${BEHIND} 个提交——先 git pull --rebase，别在旧代码上接着写"
    fi
    if [ "$AHEAD" -gt 0 ]; then
        echo "  本地领先 ${AHEAD} 个提交，还没推"
    fi
    [ "$AHEAD" = 0 ] && [ "$BEHIND" = 0 ] && echo "  与远端一致"
fi

DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"
[ "$DIRTY" -gt 0 ] && echo "  工作区有 ${DIRTY} 处未提交改动"

# 账本是另一条链路（peach.sync），和代码分开报，免得混为一谈。
if [ -x .venv/bin/python ]; then
    PYTHONPATH=src .venv/bin/python - <<'PY' 2>/dev/null || true
from peach.config import DATABASE_PATH, SHARED_DATABASE_PATH
from peach.sync import plan
decision = plan(DATABASE_PATH, SHARED_DATABASE_PATH)
print(f"账本：{decision.action} · {decision.reason}")
PY
fi
