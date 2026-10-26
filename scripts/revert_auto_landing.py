"""把自动落库写下的一批实体结果整批撤回（ADR-0052）。

自动落库的每条写入都带归属串和批次号：官网链接记在 `entity_link.metadata_json` 的
`source` 与 `batch`，标识文件记在边车 `.provenance.json` 的同名两项，别名的
`entity_alias.source` 直接就是批次号 `<source>@<任务行 id>`（ADR-0055）。女优资料行的
`performer_profile.source` 同样是批次号，后继登记的站上编号在 `entity_external_ref.metadata_json`
里记 `source` 与 `batch`（ADR-0067），番号样张的 `code_sample_image.source` 也是批次号（ADR-0068），
种子包补的所属事务所 `entity_membership.source` 与 label 的片商 `label_maker.source` 同样（ADR-0073、ADR-0075），
查创作者身份后继写的身份断言 `entity_classification.source` 也是批次号（ADR-0098）。
判据错了一批，就按它们认出来一起撤掉，不必一条条找。

复核队列的自动否决记在 `review_decision.note` 里（`auto_rejected` 与 `rule`，ADR-0079），
`--source` 给规则名就按它认。决定没有批次号，给了 `--batch` 就一条都不认。撤掉的那几行回到
复核页；下一轮处理任务会按同一条判据再否决一次，所以先改判据再撤。

人批准过的标签上按并集补进来的那几个，`asset_tag.source` 与 `asset_entity.source` 都是批次号
`auto:metadata-tags@<时间>`。撤回删掉这几行标签，并把对应决定 note 里补标签时追加的
`refreshed_candidate_key`、`added_tags` 去掉，那一行重新过期、回到复核页；生词收录后补的
那几个（ADR-0082）连同 `collected_genres` 一起去掉，下一轮按那时的收录结果重补。同样先改判据再撤。

撤回是删除，不是恢复旧值：补厂牌后继只在盘上一张图都没有、账本里一条官网都没有时才写，
补别名后继只写账本里还没有的写法，补女优资料后继只写自动来源的那一行，补样张后继只给还没有
样张的番号写，写下的就是那一格的全部，删掉就回到它写之前的样子。

已消失作品的个人记录接到新文件（ADR-0087）不是删除能撤的：搬运删了旧行，`record_rehome` 存着
旧行快照与搬动的键，批次号 `<source>@<id>`。撤回按快照重建旧行、把搬走的记录改回去，旧行的
id 或路径已被别的行占用时那一批拒绝撤回。登记时自动接回记 `auto:vanished-reattach`，孤儿记录
列表里由人接回记 `user:reattach`。

「想要」清单的入库对账记在 `want_item.acquired_source` 与 `acquired_batch`（批次号
`auto:want-acquired@<登记时刻>`）。撤回把那几条改回待找，查找计数原样留着。

西方发行方的无番号作品封面（`ASSET-ID-<id>.jpg`）在 `.scraping.json` 边车里记 `source` 与 `batch`
（`auto:western-artwork@<时间>`）。撤回删掉封面与同组边车，作品回到没有封面的样子。

关注作者建档（ADR-0096）建下的创作者在 `entity.metadata_json` 里记 `source` 与 `batch`。撤回先删
这一批的链接与别名，再解绑关注来源；实体还被别的行引用（人挂上的作品、链接）就留着，没有才删。

    revert_auto_landing.py --source auto:follow-creator
    revert_auto_landing.py --source auto:performer-alias
    revert_auto_landing.py --source auto:performer-alias --batch auto:performer-alias@812
    revert_auto_landing.py --source auto:performer-profile
    revert_auto_landing.py --source script:creator-identity-wiki
    revert_auto_landing.py --source auto:sample-images
    revert_auto_landing.py --source auto:seed --batch auto:seed@2026-09-25
    revert_auto_landing.py --source adr-0079-fc2-descriptive-performer
    revert_auto_landing.py --source auto:metadata-tags
    revert_auto_landing.py --source auto:vanished-reattach --batch auto:vanished-reattach@3
    revert_auto_landing.py --source auto:want-acquired
    revert_auto_landing.py --source auto:western-artwork --batch auto:western-artwork@20261008T000000Z

默认只列计划；`--apply` 必须同时给 `--backup`，删文件在账本行之后、同一次运行里完成。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from peach import (company_profiles, fc2_sellers, follow_creators, jav_poster_crop,  # noqa: E402
                   record_rehome, sample_images, wants, western_artwork)
from peach.config import COVER_DIR, GENERATED_DIR  # noqa: E402
from peach.metadata_auto_apply import UNION_TAGS_SOURCE  # noqa: E402
from peach.scripting import add_ledger_write_args, open_for_write, verify_after_write  # noqa: E402

SIDECARS = (".ct", ".provenance.json")
COVER_SIDECARS = (".face.json", jav_poster_crop.SIDECAR_SUFFIX, ".scraping.json")


def matches(record: dict, source: str, batch: str) -> bool:
    return record.get("source") == source and (not batch or record.get("batch") == batch)


def planned_links(connection, source: str, batch: str) -> list[dict]:
    rows = connection.execute(
        "SELECT l.id,l.entity_id,e.canonical_name,l.url,l.metadata_json FROM entity_link l"
        " JOIN entity e ON e.id=l.entity_id WHERE l.metadata_json LIKE ?",
        (f"%{source}%",)).fetchall()
    found = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            continue
        if matches(metadata, source, batch):
            found.append({"id": row["id"], "entity": row["canonical_name"], "url": row["url"],
                          "batch": metadata.get("batch", "")})
    return found


def planned_aliases(connection, source: str, batch: str) -> list[dict]:
    """这个来源写下的别名。`source` 列存的是批次号，所以不给批次时按「来源@」前缀认。"""
    clause, values = _batch_clause("a.source", source, batch)
    return [{"entity_id": row["entity_id"], "entity": row["canonical_name"], "alias": row["alias"],
             "normalized": row["normalized_alias"], "source": row["source"]}
            for row in connection.execute(
                "SELECT a.entity_id,e.canonical_name,a.alias,a.normalized_alias,a.source"
                " FROM entity_alias a JOIN entity e ON e.id=a.entity_id WHERE " + clause
                + " ORDER BY a.entity_id,a.alias", values)]


def _batch_clause(column: str, source: str, batch: str) -> tuple[str, tuple]:
    """`column` 存的是批次号：给了批次就逐字比，不给就按「来源@」前缀认。"""
    if batch:
        return f"{column}=?", (batch,)
    return f"({column}=? OR substr({column},1,?)=?)", (source, len(source) + 1, source + "@")


def planned_profiles(connection, source: str, batch: str) -> list[dict]:
    """这个来源写下的女优资料行。"""
    clause, values = _batch_clause("p.source", source, batch)
    return [{"entity_id": row["entity_id"], "entity": row["canonical_name"], "source": row["source"]}
            for row in connection.execute(
                "SELECT p.entity_id,e.canonical_name,p.source FROM performer_profile p"
                " JOIN entity e ON e.id=p.entity_id WHERE " + clause + " ORDER BY p.entity_id",
                values)]


def planned_claims(connection, source: str, batch: str) -> list[dict]:
    """这个来源写下的身份断言（`source` 列存批次号）。FC2 卖家那一批连同关系由 `fc2_sellers` 撤。"""
    if source == fc2_sellers.SOURCE:
        return []
    clause, values = _batch_clause("c.source", source, batch)
    return [{"entity_id": row["entity_id"], "entity": row["canonical_name"], "facet": row["facet"],
             "value": row["value"], "source": row["source"]}
            for row in connection.execute(
                "SELECT c.entity_id,e.canonical_name,c.facet,c.value,c.source FROM entity_classification c"
                " JOIN entity e ON e.id=c.entity_id WHERE " + clause + " ORDER BY c.entity_id,c.facet,c.value",
                values)]


def planned_memberships(connection, source: str, batch: str) -> list[dict]:
    """这个来源写下的所属事务所（`source` 列存批次号）。"""
    clause, values = _batch_clause("m.source", source, batch)
    return [{"member_id": row["member_id"], "entity": row["canonical_name"], "agency": row["agency"],
             "source": row["source"]}
            for row in connection.execute(
                "SELECT m.member_id,e.canonical_name,a.canonical_name AS agency,m.source"
                " FROM entity_membership m JOIN entity e ON e.id=m.member_id"
                " JOIN entity a ON a.id=m.agency_id WHERE " + clause + " ORDER BY m.member_id", values)]


def planned_makers(connection, source: str, batch: str) -> list[dict]:
    """这个来源写下的 label 归属片商（`source` 列存批次号）。"""
    clause, values = _batch_clause("l.source", source, batch)
    return [{"label_id": row["label_id"], "entity": row["canonical_name"], "maker": row["maker"],
             "source": row["source"]}
            for row in connection.execute(
                "SELECT l.label_id,e.canonical_name,m.canonical_name AS maker,l.source"
                " FROM label_maker l JOIN entity e ON e.id=l.label_id"
                " JOIN entity m ON m.id=l.maker_id WHERE " + clause + " ORDER BY l.label_id", values)]


def planned_refs(connection, source: str, batch: str) -> list[dict]:
    """这个来源登记的站上编号（`metadata_json` 里记着 `source` 与 `batch`）。"""
    found = []
    for row in connection.execute(
            "SELECT r.entity_id,e.canonical_name,r.provider,r.external_kind,r.external_id,"
            "r.metadata_json FROM entity_external_ref r JOIN entity e ON e.id=r.entity_id"
            " WHERE r.metadata_json LIKE ?", (f"%{source}%",)):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            continue
        if isinstance(metadata, dict) and matches(metadata, source, batch):
            found.append({"entity": row["canonical_name"], "provider": row["provider"],
                          "kind": row["external_kind"], "id": row["external_id"],
                          "batch": metadata.get("batch", "")})
    return found


def planned_rejections(connection, source: str, batch: str) -> list[dict]:
    """这条规则自动写下的复核否决（note 里 `auto_rejected` 为真、`rule` 逐字相同）。

    用户手工否决的 note 是自由文本或不带 `auto_rejected`，这里认不到，也就不会被撤。
    """
    if batch:
        return []
    found = []
    for row in connection.execute(
            "SELECT category,item_key,note FROM review_decision"
            " WHERE status='rejected' AND note LIKE ? ORDER BY category,item_key",
            (f"%{source}%",)):
        try:
            note = json.loads(row["note"] or "{}")
        except ValueError:
            continue
        if (isinstance(note, dict) and note.get("auto_rejected") is True
                and note.get("rule") == source):
            found.append({"category": row["category"], "item_key": row["item_key"]})
    return found


def planned_tags(connection, source: str, batch: str) -> list[dict]:
    """人批准过的标签上按并集补进来的那几个（`asset_entity.source` 存批次号）。

    `item_key` 取自实体关系留痕里的 `review_item`：撤掉标签的同时，那一行决定的 note
    要去掉 `refreshed_candidate_key` 与 `added_tags`，否则它不再过期，也就回不到复核页。

    只认并集补标签这一个归属串：标签的来源列还存着扫描、刮削写下的 `name`、`r18` 等，
    给别的 `--source` 就按它整批删，删掉的是不归自动落库管的标签。
    """
    if source != UNION_TAGS_SOURCE:
        return []
    clause, values = _batch_clause("ae.source", source, batch)
    found = []
    for row in connection.execute(
            "SELECT ae.asset_id,ae.entity_id,e.canonical_name,ae.source,ae.metadata_json"
            " FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id"
            " WHERE ae.role='tag' AND " + clause + " ORDER BY ae.asset_id,e.canonical_name",
            values):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except ValueError:
            metadata = {}
        found.append({"asset_id": row["asset_id"], "entity_id": row["entity_id"],
                      "tag": row["canonical_name"], "source": row["source"],
                      "item_key": str(metadata.get("review_item") or "")
                      if isinstance(metadata, dict) else ""})
    return found


def reopen_extended_decisions(connection, item_keys: set[str]) -> int:
    """并集补标签的那几行决定去掉补标签时追加的几项，其余原样，返回改了几行。"""
    changed = 0
    for item_key in sorted(key for key in item_keys if key):
        row = connection.execute(
            "SELECT note FROM review_decision WHERE category='metadata_fields' AND item_key=?",
            (item_key,)).fetchone()
        try:
            note = json.loads(row["note"] or "{}") if row else None
        except ValueError:
            note = None
        if not isinstance(note, dict) or "added_tags" not in note:
            continue
        kept = {key: value for key, value in note.items()
                if key not in {"added_tags", "refreshed_candidate_key", "collected_genres"}}
        connection.execute(
            "UPDATE review_decision SET note=? WHERE category='metadata_fields' AND item_key=?",
            (json.dumps(kept, ensure_ascii=False, separators=(",", ":")), item_key))
        changed += 1
    return changed


def planned_files(logo_root: Path, source: str, batch: str) -> list[Path]:
    """边车上写着这个来源的标识文件本体。"""
    found = []
    for sidecar in sorted(logo_root.glob("*.provenance.json")):
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if matches(record, source, batch):
            found.append(sidecar.with_name(sidecar.name.removesuffix(".provenance.json")))
    return found


def print_acquired(planned: list[dict]) -> None:
    """想要清单里对账标了已入库的那几条，一条一行。"""
    for item in planned:
        label = item["code"] or item["title"] or f"#{item['id']}"
        print(f" - 想要 {label[:40]:<40} 资产 {item['acquired_asset_id']} {item['acquired_batch']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument("--source", default="auto:studio-mark",
                        help="要撤回的归属串，与写入时记的逐字相同")
    parser.add_argument("--batch", default="", help="只撤这一批；不给就撤这个来源的全部")
    parser.add_argument("--logo-root", type=Path, default=GENERATED_DIR / "logos")
    parser.add_argument("--cover-root", type=Path, default=COVER_DIR)
    return parser


def print_profiles(profiles: list[dict], companies: list[dict]) -> None:
    """显示逐行人物资料与逐字段公司资料的撤回清单。"""
    for profile in profiles:
        print(f" - 资料 {profile['entity'][:20]:<20} {profile['source']}")
    for company in companies:
        print(f" - 公司资料 {company['entity']}: {','.join(company['fields'])}")


def print_files(files: list[Path], covers: list[Path]) -> None:
    """显示要删的标识文件与无番号作品封面。"""
    for path in files:
        print(f" - 标识 {path.name}")
    for path in covers:
        print(f" - 封面 {path.name}")


def remove_files(files: list[Path], covers: list[Path]) -> int:
    """删掉标识文件与作品封面，连同各自的同组边车；返回删掉的文件数。"""
    targets = [target for path in files
               for target in (path, *(path.with_name(path.name + suffix) for suffix in SIDECARS))]
    targets += [target for path in covers
                for target in (path, *(path.with_suffix(suffix) for suffix in COVER_SIDECARS))]
    removed = 0
    for target in targets:
        if target.exists():
            target.unlink()
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = open_for_write(args)
    try:
        links = planned_links(connection, args.source, args.batch)
        aliases = planned_aliases(connection, args.source, args.batch)
        profiles = planned_profiles(connection, args.source, args.batch)
        claims = planned_claims(connection, args.source, args.batch)
        companies = company_profiles.planned_revert(connection, args.source, args.batch)
        sellers = fc2_sellers.planned_revert(connection, args.source, args.batch)
        creators = follow_creators.planned_revert(connection, args.source, args.batch)
        refs = planned_refs(connection, args.source, args.batch)
        memberships = planned_memberships(connection, args.source, args.batch)
        makers = planned_makers(connection, args.source, args.batch)
        files = planned_files(args.logo_root, args.source, args.batch)
        covers = western_artwork.planned_covers(args.cover_root, args.source, args.batch)
        samples = sample_images.planned_revert(connection, args.source, args.batch)
        rejections = planned_rejections(connection, args.source, args.batch)
        tags = planned_tags(connection, args.source, args.batch)
        rehomes = record_rehome.planned_revert(connection, args.source, args.batch)
        acquired = wants.planned_revert(connection, args.source, args.batch)
        print_acquired(acquired)
        for link in links:
            print(f" - 链接 {link['entity'][:20]:<20} {link['url'][:56]} {link['batch']}")
        for alias in aliases:
            print(f" - 别名 {alias['entity'][:20]:<20} {alias['alias'][:40]} {alias['source']}")
        print_profiles(profiles, companies)
        for creator in creators:
            print(f" - 建档 {creator['name'][:20]:<20} {creator['sources']} 个来源 {creator['batch']}")
        for claim in claims:
            print(f" - 断言 {claim['entity'][:20]:<20} {claim['facet']}={claim['value']} {claim['source']}")
        for ref in refs:
            print(f" - 编号 {ref['entity'][:20]:<20} {ref['provider']} {ref['id']} {ref['batch']}")
        for membership in memberships:
            print(f" - 归属 {membership['entity'][:20]:<20} {membership['agency'][:30]} {membership['source']}")
        for maker in makers:
            print(f" - 片商 {maker['entity'][:20]:<20} {maker['maker'][:30]} {maker['source']}")
        print_files(files, covers)
        for sample in samples:
            print(f" - 样张 {sample['code']:<20} {sample['count']} 张 {sample['source']}")
        for rejection in rejections:
            print(f" - 否决 {rejection['category']} {rejection['item_key']}")
        for tag in tags:
            print(f" - 标签 {tag['asset_id']:<8} {tag['tag'][:30]:<30} {tag['source']}")
        for rehome in rehomes:
            print(f" - 接回 {rehome['old_asset_id']:<8} → {rehome['new_asset_id']:<8} "
                  f"{rehome['name'][:40]} {rehome['batch']}")
        print({"链接": len(links), "别名": len(aliases), "资料": len(profiles), "断言": len(claims),
               "公司资料": len(companies), "卖家": len(sellers), "关注建档": len(creators), "编号": len(refs),
               "归属": len(memberships), "片商": len(makers), "标识文件": len(files), "封面": len(covers),
               "样张": sum(sample["count"] for sample in samples), "否决": len(rejections),
               "标签": len(tags), "接回": len(rehomes), "想要入库": len(acquired)})
        if not args.apply:
            print("dry-run；确认无误后加 --apply --backup <路径>")
            return 0
        with connection:
            company_profiles.revert(connection, companies)
            connection.executemany("DELETE FROM entity_link WHERE id=?",
                                   [(link["id"],) for link in links])
            connection.executemany(
                "DELETE FROM entity_alias WHERE entity_id=? AND normalized_alias=? AND source=?",
                [(alias["entity_id"], alias["normalized"], alias["source"]) for alias in aliases])
            connection.executemany(
                "DELETE FROM performer_profile WHERE entity_id=? AND source=?",
                [(profile["entity_id"], profile["source"]) for profile in profiles])
            connection.executemany(
                "DELETE FROM entity_classification WHERE entity_id=? AND facet=? AND value=? AND source=?",
                [(claim["entity_id"], claim["facet"], claim["value"], claim["source"]) for claim in claims])
            connection.executemany(
                "DELETE FROM entity_external_ref WHERE provider=? AND external_kind=?"
                " AND external_id=?", [(ref["provider"], ref["kind"], ref["id"]) for ref in refs])
            connection.executemany(
                "DELETE FROM entity_membership WHERE member_id=? AND source=?",
                [(membership["member_id"], membership["source"]) for membership in memberships])
            connection.executemany(
                "DELETE FROM label_maker WHERE label_id=? AND source=?",
                [(maker["label_id"], maker["source"]) for maker in makers])
            removed_samples = sample_images.revert(connection, args.source, args.batch)
            connection.executemany(
                "DELETE FROM review_decision WHERE category=? AND item_key=? AND status='rejected'",
                [(rejection["category"], rejection["item_key"]) for rejection in rejections])
            connection.executemany(
                "DELETE FROM asset_entity WHERE asset_id=? AND entity_id=? AND role='tag'"
                " AND source=?", [(tag["asset_id"], tag["entity_id"], tag["source"]) for tag in tags])
            # 扁平投影只删计划里列出的那几行：按来源串整表删的话，`--source name` 这类
            # 扫描写下的标签来源也会被认成一批。
            removed_tag_rows = sum(connection.execute(
                "DELETE FROM asset_tag WHERE asset_id=? AND tag=? AND source=?",
                (tag["asset_id"], tag["tag"], tag["source"])).rowcount or 0 for tag in tags)
            reopened = reopen_extended_decisions(connection, {tag["item_key"] for tag in tags})
            restored = record_rehome.revert(connection, args.source, args.batch)
            reopened_wants = wants.revert(connection, args.source, args.batch)
            removed_seller_relations = fc2_sellers.revert(connection, sellers)
            # 链接与别名已在上面按批次删掉，剩下没有别的引用的实体才删得掉。
            creator_revert = follow_creators.revert(connection, creators)
        integrity, orphans = verify_after_write(connection)
    finally:
        connection.close()
    removed = remove_files(files, covers)
    print({"删除链接": len(links), "删除别名": len(aliases), "删除资料": len(profiles), "删除断言": len(claims),
           "删除编号": len(refs), "删除归属": len(memberships), "删除片商": len(makers),
           "删除样张": removed_samples, "删除否决": len(rejections),
           "删除标签": len(tags), "删除扁平标签": removed_tag_rows, "重开决定": reopened,
           "撤回接回": restored, "想要回到待找": reopened_wants, "卖家关系": removed_seller_relations,
           "解绑关注来源": creator_revert["unbound"], "删除建档实体": creator_revert["removed"], "删除文件": removed,
           "integrity_check": integrity, "foreign_key_check": orphans})
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
