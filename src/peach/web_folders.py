"""原目录浏览与用户确认的目录用途契约。"""
from . import folder_rules, media_folders, settings_file


def _index(contract, args):
    config = settings_file.active()
    scope = (args.get('loc', ''), args.get('library', ''), repr(config.locations), repr(config.library_names))
    def read():
        with contract.read_connection() as connection:
            return media_folders.directory_index(connection, config, locations=scope[0], library=scope[1])
    return contract.cached_until_changed('folders:' + repr(scope), read, tables=('asset',))


def folder_filter(contract, args):
    """目录筛选按注册路径的完整层级边界限定作品范围。"""
    if not args.get('folder'):
        return [], []
    node = _index(contract, {}).get(args['folder'])
    if node is None:
        return ['0'], []
    clause, values = media_folders.path_predicate(
        node['location'], node['path'], args.get('folder_recursive') != '0')
    return [clause], values


def q_folders(contract, args):
    index = _index(contract, args)
    result = media_folders.browse(index, args.get('folder', ''), str(args.get('q', ''))[:200])
    with contract.read_connection() as connection:
        _, data = folder_rules.load(connection)
        rule = next((row for row in data['rules'] if row['id'] == args.get('folder')), None)
        result['conflicts'] = folder_rules.plan(connection, rule, data)['conflicts'] if rule else []
    result['rule'] = rule
    result['rules_revision'] = data['revision']
    result['rules'] = [row for row in data['rules'] if row['id'] in index]
    return result


def w_folder_preview(contract, body):
    node = _index(contract, {}).get(body.get('folder'))
    if node is None:
        raise ValueError('目录不存在或已移出媒体库')
    with contract.read_connection() as connection:
        _, data = folder_rules.load(connection)
        rule = folder_rules.validate(connection, node, body)
        preview = folder_rules.plan(connection, rule, data)
    return {key: value for key, value in preview.items() if key != 'rows'}


def w_folder_rule(contract, body):
    node = _index(contract, {}).get(body.get('folder'))
    if node is None:
        raise ValueError('目录不存在或已移出媒体库')
    with contract.write_transaction() as connection:
        settings, data = folder_rules.load(connection)
        if type(body.get('expected_revision')) is not int or data['revision'] != body['expected_revision']:
            raise folder_rules.FolderRuleConflict('目录规则已变化，请重新打开设置')
        if body.get('action') == 'remove':
            result = {'removed': folder_rules.remove(connection, settings, data, node['id'])}
        elif body.get('action') == 'save':
            rule = folder_rules.validate(connection, node, body)
            preview = folder_rules.plan(connection, rule, data)
            result = {'applied': folder_rules.save(connection, settings, data, rule, preview, body.get('plan_version')),
                      'conflicts': len(preview['conflicts'])}
        else:
            raise ValueError('不支持的目录规则操作')
    return {'ok': True, **result}
