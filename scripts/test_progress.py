"""保留完整测试批次中未受影响的文件结果，不延长其有效期。"""
from __future__ import annotations

from itertools import combinations

from . import change_impact


def names(select, scopes):
    return {path.name for scope in scopes for path in select(scope)}


def affected(record, context, resolve, select):
    if record.get("environment") != context["environment"]:
        return names(select, ("full",))
    paths, kinds = change_impact.delta(record["manifest"], context["manifest"])
    scopes, _ = resolve(paths, kinds=kinds) if paths else ((), "")
    return names(select, scopes)


def retained(record, context, resolve, select):
    """只有完整批次的文件结果能组成 full；局部测试不能累加冒充完整批次。"""
    if not record.get("cohort") or not record.get("coverage"):
        return {}
    invalid = affected(record, context, resolve, select)
    required = names(select, ("full",))
    return {name: stamp for name, stamp in record["coverage"].items()
            if name not in invalid and name in required}


def completion(coverage, select):
    required = names(select, ("full",))
    return bool(required) and required.issubset(coverage)


def pending(coverage, select, scopes):
    """选择覆盖缺少文件、涉及测试文件最少的域组合；未知文件回到 full。"""
    missing = names(select, ("full",)) - coverage.keys()
    if not missing:
        return ()
    candidates = {scope: names(select, (scope,)) for scope in scopes}
    candidates = {scope: files for scope, files in candidates.items() if files & missing}
    if not missing.issubset(set().union(*candidates.values())):
        return ("full",)
    best = None
    for count in range(1, len(candidates) + 1):
        for chosen in combinations(candidates, count):
            files = set().union(*(candidates[scope] for scope in chosen))
            if missing.issubset(files):
                cost = (len(files), len(chosen), chosen)
                if best is None or cost < best:
                    best = cost
    return best[2]


def after_run(context, scopes, passed, previous, baseline, select, stamp):
    """只接收同一快照上的成功结果；失败会清除本次范围及 full 声明。"""
    source = baseline or previous
    cohort = source.get("cohort")
    coverage = dict(source.get("coverage", {})) if cohort else {}
    tested = names(select, scopes)
    if "full" in scopes:
        cohort = context["state"] if passed else None
        coverage = {}
    if passed and cohort:
        coverage.update(dict.fromkeys(tested, stamp))
    else:
        coverage = {name: when for name, when in coverage.items() if name not in tested}
    return cohort, coverage
