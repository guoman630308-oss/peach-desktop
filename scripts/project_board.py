"""按 GitHub 事实同步项目状态、关联实现与已发布版本。默认只预览。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github/project-board.json"


def gh(*args, payload=None):
    command = ["gh", *args]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=None if payload is None else json.dumps(payload),
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if result.returncode:
        raise RuntimeError(f"GitHub CLI 调用失败：{result.stderr.strip()} {result.stdout[:2000]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def graphql(query, **variables):
    result = gh("api", "graphql", payload={"query": query, "variables": variables})
    if result.get("errors"):
        raise RuntimeError(json.dumps(result["errors"], ensure_ascii=False))
    return result["data"]


def connection(query, path, **variables):
    """逐页读取，分页游标必须前进。"""
    nodes, cursor = [], None
    while True:
        data = graphql(query, cursor=cursor, **variables)
        for key in path:
            data = data[key]
        nodes.extend(data["nodes"])
        page = data["pageInfo"]
        if not page["hasNextPage"]:
            return nodes
        if not page["endCursor"] or page["endCursor"] == cursor:
            raise RuntimeError("GitHub 分页游标没有前进")
        cursor = page["endCursor"]


def ancestor(commit, ref):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    result = subprocess.run(["git", "merge-base", "--is-ancestor", commit, ref],
                            cwd=ROOT, capture_output=True, timeout=30)
    if result.returncode not in (0, 1, 128):
        raise RuntimeError("提交归属验证失败")
    return result.returncode == 0


def references(body, repository, allowed=None):
    """只接受独立的关联行，不把正文中随手提到的编号当成交付。"""
    found, allowed = set(), {name.casefold(): name for name in (allowed or [repository])}
    for line in body.splitlines():
        if not re.match(r"^\s*(?:refs?|closes?|fix(?:es)?|resolves?)\s*[:：]?\s+", line, re.I):
            continue
        matches = re.findall(r"(?:(\w[\w.-]*/[\w.-]+))?#(\d+)", line)
        matches += re.findall(r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)", line)
        for repo, number in matches:
            repo = allowed.get((repo or repository).casefold())
            if repo:
                found.add(f"{repo}#{number}")
    return found


def issue_key(content):
    """仓库名与编号共同标识 Issue，避免跨仓库编号相同。"""
    return f'{content["repository"]["nameWithOwner"]}#{content["number"]}'


def canonical(key, aliases):
    seen = set()
    while key in aliases:
        if key in seen:
            raise ValueError("Issue 迁移映射存在循环")
        seen.add(key)
        key = aliases[key]
    return key


def values(item):
    return {value["field"]["name"]: value.get("name", value.get("text", ""))
            for value in item.get("fieldValues", {}).get("nodes", []) if value}


def decision(content, current, linked, published):
    """状态服从关闭原因与验收；交付进度单独呈现。"""
    is_pr = content["__typename"] == "PullRequest"
    merged = bool(content.get("merged")) if is_pr else any(pr.get("merged") for pr in linked)
    labels = {label["name"] for label in content.get("labels", {}).get("nodes", [])}
    status = current.get("Status", "待评估")
    if is_pr:
        status = "已完成" if merged else ("已关闭未合入" if content["state"] == "CLOSED" else "进行中")
    elif content["state"] == "CLOSED":
        status = "已取消" if content.get("stateReason") == "NOT_PLANNED" else "已完成"
    elif "status:needs-verification" in labels:
        status = "待验证"
    elif "status:needs-info" in labels:
        status = "待评估"
    elif merged:
        status = "待验证"
    elif linked:
        status = "进行中"
    elif status in {"已完成", "已取消"}:
        status = "待安排"
    delivery = "已发布" if published else ("已合入待发布" if merged else (
        "尚未合入" if linked or is_pr else "未关联实现"))
    result = {"Status": status, "交付进度": delivery, "发布版本": "", "发布说明": "", "实现关联": ""}
    if published:
        result.update({"发布版本": published["tag_name"], "发布说明": published["html_url"]})
    return result


CONTENT = '''__typename ... on DraftIssue{id title} ... on Issue{id number title url state stateReason repository{nameWithOwner}
labels(first:100){nodes{name}}}
... on PullRequest{id number title url state merged mergeCommit{oid} body repository{nameWithOwner}
closingIssuesReferences(first:100){nodes{id number repository{nameWithOwner}}}}'''
PR_CONTENT = '''__typename id number title url state merged mergeCommit{oid} body repository{nameWithOwner}
closingIssuesReferences(first:100){nodes{id number repository{nameWithOwner}}}'''


def read_project(project):
    fields = graphql('''query($id:ID!){node(id:$id){... on ProjectV2{
fields(first:100){nodes{... on ProjectV2Field{id name dataType}
... on ProjectV2SingleSelectField{id name options{id name}}}}}}}''', id=project)["node"]["fields"]["nodes"]
    fields = {field["name"]: field for field in fields if "name" in field}
    for required in ("Status", "交付进度", "发布版本", "发布说明", "实现关联"):
        if required not in fields:
            raise ValueError(f"看板缺少字段：{required}")
    items = connection('''query($id:ID!,$cursor:String){node(id:$id){... on ProjectV2{
items(first:100,after:$cursor,archivedStates:[ARCHIVED,NOT_ARCHIVED]){nodes{id isArchived content{''' + CONTENT + '''}
fieldValues(first:100){nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2SingleSelectField{name}}}
... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2Field{name}}}}}}
pageInfo{hasNextPage endCursor}}}}}''', ("node", "items"), id=project)
    return fields, items


def pull_requests(repository):
    owner, name = repository.split("/")
    return connection('''query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){
pullRequests(first:100,after:$cursor,orderBy:{field:CREATED_AT,direction:DESC}){nodes{''' + PR_CONTENT + '''}
pageInfo{hasNextPage endCursor}}}}''', ("repository", "pullRequests"), owner=owner, name=name)


def issue_links(prs, config):
    linked = {}
    for pr in prs:
        if pr["state"] == "CLOSED" and not pr.get("merged"):
            continue
        allowed = config.get("issue_repositories", [config["repository"]])
        keys = {issue_key(issue) for issue in pr["closingIssuesReferences"]["nodes"]
                if issue["repository"]["nameWithOwner"] in allowed}
        keys |= references(pr["body"], config["repository"], config.get("issue_repositories"))
        for key in {canonical(key, config.get("issue_aliases", {})) for key in keys}:
            linked.setdefault(key, []).append(pr)
    return linked


def incoming_issues(config):
    incoming = []
    for repository in config.get("issue_repositories", [config["repository"]]):
        owner, name = repository.split("/")
        incoming.extend(connection('''query($owner:String!,$name:String!,$states:[IssueState!],$cursor:String){repository(owner:$owner,name:$name){
issues(first:100,after:$cursor,states:$states){nodes{__typename id number title url state stateReason repository{nameWithOwner}
labels(first:100){nodes{name}}}pageInfo{hasNextPage endCursor}}}}''',
                                   ("repository", "issues"), owner=owner, name=name,
                                   states=None if repository == config.get("planning_repository") else ["OPEN"]))
    return incoming


def include_incoming(config, candidates, prs, event, apply):
    repository, project = config["repository"], config["project_id"]
    incoming = incoming_issues(config)
    tracked = {issue_key(item["content"]) for item in candidates.values()
               if not item["isArchived"] and item["content"]["__typename"] == "Issue"}
    incoming += [pr for pr in prs if pr["state"] == "OPEN" or any(
        issue_key(issue) in tracked for issue in pr["closingIssuesReferences"]["nodes"])]
    subject = (event or {}).get("issue") or (event or {}).get("pull_request")
    if subject and (event or {}).get("repository", {}).get("full_name") == repository:
        node = graphql('''query($id:ID!){node(id:$id){''' + CONTENT + '''}}''', id=subject["node_id"])["node"]
        incoming.append(node)
    for node in incoming:
        if config.get("issues_only") and node["__typename"] != "Issue":
            continue
        if node["id"] not in candidates:
            item = {"id": None, "content": node, "fieldValues": {"nodes": []}, "isArchived": False}
            if apply:
                item["id"] = graphql('''mutation($project:ID!,$content:ID!){addProjectV2ItemById(
input:{projectId:$project,contentId:$content}){item{id}}}''', project=project, content=node["id"])["addProjectV2ItemById"]["item"]["id"]
            candidates[node["id"]] = item


def published_releases(repository):
    pages = gh("api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp")
    return sorted([release for page in pages for release in page
                       if not release["draft"] and release.get("published_at") and release.get("assets")],
                  key=lambda release: release["published_at"])


def delivery_fields(config, content, current, linked, releases):
    repository = config["repository"]
    is_pr = content["__typename"] == "PullRequest"
    associated = [content] if is_pr else linked.get(issue_key(content), [])
    commits = [pr["mergeCommit"]["oid"] for pr in associated if pr.get("merged") and pr.get("mergeCommit")]
    commits += config.get("implementation_commits", {}).get(issue_key(content), []) if not is_pr else []
    integrated = [commit for commit in commits if ancestor(commit, "origin/master")]
    if integrated and not is_pr and not any(pr.get("merged") for pr in associated):
        associated = [*associated, {"merged": True}]
    published = next((release for release in releases if any(
        ancestor(commit, f'refs/tags/{release["tag_name"]}') for commit in integrated)), None)
    desired = decision(content, current, associated, published)
    urls = [pr["url"] for pr in associated if pr.get("url")]
    if urls:
        desired["实现关联"] = "\n".join(urls)
    elif integrated:
        desired["实现关联"] = "\n".join(f"https://github.com/{repository}/commit/{commit}" for commit in integrated)
    return desired


def apply_fields(project, item, fields, desired, apply):
    current, content = values(item), item["content"]
    changes = []
    for field_name, value in desired.items():
        if current.get(field_name, "") == value:
            continue
        field = fields[field_name]
        update = {"singleSelectOptionId": next(option["id"] for option in field["options"] if option["name"] == value)} if "options" in field else {"text": value}
        changes.append({"repository": content["repository"]["nameWithOwner"], "number": content["number"],
                        "title": content["title"], "field": field_name, "value": value})
        if apply:
            if not value:
                graphql('''mutation($project:ID!,$item:ID!,$field:ID!){clearProjectV2ItemFieldValue(
input:{projectId:$project,itemId:$item,fieldId:$field}){projectV2Item{id}}}''',
                        project=project, item=item["id"], field=field["id"])
                continue
            graphql('''mutation($project:ID!,$item:ID!,$field:ID!,$value:ProjectV2FieldValue!){
updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,value:$value}){projectV2Item{id}}}''',
                    project=project, item=item["id"], field=field["id"], value=update)
    return changes


def reconcile(config, *, apply=False, event=None):
    project, repository = config["project_id"], config["repository"]
    fields, items = read_project(project)
    prs = pull_requests(repository)
    linked = issue_links(prs, config)
    candidates = {item["content"]["id"]: item for item in items if item.get("content")}
    include_incoming(config, candidates, prs, event, apply)
    releases = published_releases(repository)
    changes = []
    for item in candidates.values():
        content = item["content"]
        if item["isArchived"] or content["__typename"] not in {"Issue", "PullRequest"}:
            continue
        content_repo = content["repository"]["nameWithOwner"]
        if content_repo not in config.get("issue_repositories", [repository]):
            continue
        desired = delivery_fields(config, content, values(item), linked, releases)
        if "来源" in fields:
            desired["来源"] = "维护者待办" if content_repo == config.get("planning_repository") else "用户反馈"
        changes.extend(apply_fields(project, item, fields, desired, apply))
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--event", type=Path)
    args = parser.parse_args()
    event = json.loads(args.event.read_text(encoding="utf-8")) if args.event else None
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if os.environ.get("GITHUB_REPOSITORY", config["repository"]) != config["repository"]:
        raise ValueError("仓库不匹配，拒绝同步项目")
    changes = reconcile(config, apply=args.apply, event=event)
    print(json.dumps({"apply": args.apply, "changes": changes}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
