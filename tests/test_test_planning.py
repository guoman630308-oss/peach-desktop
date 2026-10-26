"""验证选测边界、分片完整性与临时账本隔离。"""
from pathlib import Path
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import ci_plan, test_runner as runner
from support.ledger import fresh_ledger


class CIFrontendOwnershipTests(unittest.TestCase):
    CI = {'GITHUB_ACTIONS': 'true', 'GITHUB_WORKFLOW': 'Test', 'GITHUB_JOB': 'python'}

    @staticmethod
    def ids(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                yield from CIFrontendOwnershipTests.ids(test)
            else:
                yield test.id()

    def frontend_ids(self, environment, **shard):
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(runner, 'selected_files', return_value=(runner.TESTS / 'test_frontend_build.py',)):
            return list(self.ids(runner.build_suite('web', **shard)))

    def test_only_known_ci_jobs_delegate_exactly_three_frontend_tools(self):
        local = set(self.frontend_ids({}))
        delegated = {f'test_frontend_build.VitestTests.{name}' for name in (
            'test_the_frontend_sources_typecheck', 'test_frontend_sources_and_tests_pass_lint',
            'test_the_island_suite_passes')}
        self.assertLessEqual(delegated, local)
        for job in ('python', 'web-e2e'):
            with self.subTest(job=job):
                selected = set(self.frontend_ids({**self.CI, 'GITHUB_JOB': job}))
                self.assertEqual(local - selected, delegated)
                self.assertEqual(selected - local, set())
                self.assertIn('test_frontend_build.VitestTests.test_the_island_suite_has_a_bounded_worker_pool', selected)
        for environment in ({}, {'GITHUB_ACTIONS': 'true'},
                            {**self.CI, 'GITHUB_ACTIONS': 'false'},
                            {**self.CI, 'GITHUB_WORKFLOW': 'Release'},
                            {**self.CI, 'GITHUB_JOB': 'another-job'}):
            with self.subTest(environment=environment):
                self.assertEqual(set(self.frontend_ids(environment)), local)

    def test_delegated_shards_cover_all_remaining_tests_once(self):
        serial = self.frontend_ids(self.CI)
        shards = [self.frontend_ids(self.CI, shard_index=index, shard_count=2) for index in range(2)]
        self.assertFalse(set(shards[0]) & set(shards[1]))
        self.assertCountEqual(serial, shards[0] + shards[1])
        self.assertEqual(len(serial), len(set(serial)))

    def test_python_jobs_delegate_browser_cases_and_keep_their_contract_tests(self):
        def collect(environment):
            with patch.dict(os.environ, environment, clear=True), patch.object(
                    runner, 'selected_files', return_value=(runner.TESTS / 'test_web_e2e.py',)):
                return set(self.ids(runner.build_suite('web')))
        local = collect({})
        selected = collect(self.CI)
        self.assertEqual(local - selected, {
            'test_web_e2e.WebE2ESmokeTests.test_every_route_holds_the_layout_and_runtime_invariants',
            'test_web_e2e.SetupE2ETests.test_the_first_run_page_writes_errors_back_and_finishes',
        })
        self.assertTrue(any('.MissingPrerequisiteTests.' in name for name in selected))
        for environment in ({**self.CI, 'GITHUB_JOB': 'web-e2e'},
                            {**self.CI, 'GITHUB_WORKFLOW': 'Release'}):
            self.assertEqual(collect(environment), local)

    def test_delegated_ci_returns_results_without_local_evidence(self):
        for scope in ('web', 'full'):
            for success, count, expected in ((True, 3, 0), (False, 3, 1), (True, 0, 1)):
                with self.subTest(scope=scope, success=success, count=count), \
                     patch.dict(os.environ, self.CI, clear=True), \
                     patch.object(runner, 'environment_preflight'), \
                     patch.object(runner, 'run_local_suite', return_value=(success, count, [])), \
                     patch.object(runner, 'verified_run', side_effect=AssertionError('CI 不签本机记录')), \
                     patch.object(runner.test_evidence, 'held', side_effect=AssertionError('CI 不取本机证据锁')), \
                     patch.object(runner.test_evidence, 'write', side_effect=AssertionError('CI 不写本机记录')), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main(['--scope', scope]), expected)

    def test_local_runs_use_the_verification_path(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(runner, 'environment_preflight'), \
             patch.object(runner, 'verified_run', return_value=0) as verify, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(['--scope', 'web']), 0)
        verify.assert_called_once()


class TestPlanningTests(unittest.TestCase):
    def test_comments_and_display_metadata_select_checks_without_hiding_logic(self):
        fixtures = {
            'tests/support/ledger.py': ('x = 1\n', '# 说明\nx = 1\n'),
            'scripts/test_runner.py': ('x = 1\n', 'x = 1 # 说明\n'),
            'frontend/package.json': ('{"description":"a","scripts":{"test":"x"}}',
                                      '{"description":"b","scripts":{"test":"x"}}'),
            'pyproject.toml': ('[project]\ndependencies=[]\n', '# 说明\n[project]\ndependencies=[]\n'),
        }
        for path, content in fixtures.items():
            with self.subTest(path=path):
                self.assertEqual(runner.scopes_for_changes([path], contents={path: content})[0], ('checks',))
                plan = ci_plan.plan('pull_request', [path], {path: content})
                self.assertFalse(plan['browser'])
                self.assertFalse(plan['frontend'])
        for content in (('x = 1', 'x = 2'), ('"help a"', '"help b"'), ('x = 1', 'x = (')):
            self.assertEqual(runner.scopes_for_changes(['tests/support/ledger.py'],
                                                     contents={'tests/support/ledger.py': content})[0], ('full',))

    def test_ci_jobs_follow_their_own_inputs(self):
        for path in ('tests/test_follow_store.py', 'tests/test_metadata_sources.py',
                     '.github/workflows/project-board.yml'):
            with self.subTest(path=path):
                plan = ci_plan.plan('pull_request', [path])
                self.assertFalse(plan['browser'])
                self.assertFalse(plan['wheel'])
        for path in ('src/peach/server.py', 'src/peach/auth.py', 'frontend/src/core/index.ts',
                     'tests/test_web_e2e.py', 'frontend/e2e/setup.test.ts'):
            with self.subTest(path=path):
                self.assertTrue(ci_plan.plan('pull_request', [path])['browser'])
        self.assertTrue(ci_plan.plan('pull_request', ['.github/workflows/release.yml'])['wheel'])
        self.assertTrue(ci_plan.plan('pull_request', ['.github/workflows/upgrade-smoke.yml'])['wheel'])
        self.assertTrue(ci_plan.plan('pull_request', ['.github/workflows/unknown.yml'])['wide'])

    def setUp(self):
        environment = patch.dict(os.environ, {'PEACH_TEST_FOCUS': '', 'PEACH_E2E_FILES': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def test_every_supplementary_test_id_names_a_real_test(self):
        """域的补充用例按名字装载：被引用的用例改名后，装进来的是一个必然报错的占位用例，
        选到这一域的 auto 都签不出记录。在这里按名字装一遍，改名当场就红。"""
        loader = unittest.TestLoader()
        sys.path[:0] = [str(runner.ROOT), str(runner.TESTS)]
        try:
            for scope, test_ids in runner.SCOPE_TEST_IDS.items():
                for test_id in test_ids:
                    with self.subTest(scope=scope, test_id=test_id):
                        loaded = list(loader.loadTestsFromName(test_id))
                        self.assertEqual([test.id().split(".", 1)[1] for test in loaded], [test_id.split(".", 1)[1]])
        finally:
            del sys.path[:2]

    def test_unspawnable_tools_stop_before_any_test_shard_starts(self):
        output = io.StringIO()
        with patch.object(runner.test_evidence, 'unspawnable_tools', return_value=('uv',)), \
             patch.object(runner, 'build_suite', side_effect=AssertionError('环境不成立时不应加载测试')), \
             contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(SystemExit, '3'):
                runner.main(['--scope', 'checks'])
        self.assertIn('uv', output.getvalue())
        self.assertIn('正常 PowerShell 权限', output.getvalue())

    def test_a_tool_this_scope_never_starts_only_warns(self):
        """受限环境里起不动的工具，只有本次真会用到时才判失败。

        `checks` 一个媒体用例都不加载，让媒体工具的权限错误挡住它，等于把人赶出正式
        入口去手拼命令。工具仍然报出来，后面真出问题时看得见这一行。
        """
        # 名字拼出来：判据是「选中的测试源码里出现了这个工具名」，而本文件自己就在
        # `checks` 域里，直接写出来这一域就自证需要它了。
        blocked = 'ff' + 'mpeg'
        self.assertNotIn(blocked, runner.tools_needed_by(('checks',)))
        self.assertIn(blocked, runner.tools_needed_by(('media',)))
        output = io.StringIO()
        with patch.object(runner.test_evidence, 'unspawnable_tools', return_value=(blocked,)), \
             contextlib.redirect_stdout(output):
            runner.environment_preflight(('checks',))
            with self.assertRaisesRegex(SystemExit, '3'):
                runner.environment_preflight(('media',))
        self.assertIn('本次范围不需要', output.getvalue())

    def test_shards_and_the_explicit_switch_skip_the_preflight(self):
        """分片子进程的域由父进程定下，父进程已经查过同一套工具；开关留给判断过的人。"""
        with patch.object(runner.test_evidence, 'unspawnable_tools', return_value=('git',)), \
             contextlib.redirect_stdout(io.StringIO()):
            runner.environment_preflight(('checks',), shard_count=2)
            with self.assertRaisesRegex(SystemExit, '3'):
                runner.environment_preflight(('checks',), shard_count=1)
        switched = io.StringIO()
        with patch.object(runner.test_evidence, 'unspawnable_tools', return_value=('git',)), \
             patch.dict(os.environ, {'PEACH_SKIP_PREFLIGHT': '1'}), \
             contextlib.redirect_stdout(switched):
            runner.environment_preflight(('checks',))
        self.assertIn('PEACH_SKIP_PREFLIGHT=1', switched.getvalue())

    def test_dependency_changes_require_full_but_tool_version_does_not(self):
        source = '[project]\nrequires-python=">=3.12"\ndependencies=["demo==1"]\n[tool.uv]\nrequired-version="==1"\n'
        for replacement, expected in ((source.replace('==1"\n', '==2"\n'), ('packaging',)),
                                      (source.replace('demo==1', 'demo==2'), ('full',)),
                                      (source.replace('>=3.12', '>=3.14'), ('full',)),
                                      ('[invalid', ('full',))):
            with self.subTest(replacement=replacement):
                scopes, _ = runner.scopes_for_changes(['pyproject.toml'], contents={
                    'pyproject.toml': (source, replacement)})
                self.assertEqual(scopes, expected)
        self.assertEqual(runner.scopes_for_changes(['pyproject.toml'])[0], ('full',))

    def test_matrix_keeps_full_mainline_and_platform_coverage(self):
        result = ci_plan.plan('push', ['web/app.js'])
        rows = result['matrix']['include']
        self.assertEqual(len([r for r in rows if r['scope'] == 'full']), 6)
        self.assertEqual({r['os'] for r in rows}, {'windows-latest', 'macos-latest'})
        self.assertEqual({r['python'] for r in rows}, {'3.14'})
        self.assertTrue(all(r['scope'] == 'full' for r in rows))
        self.assertEqual(len(result['wheel_matrix']['include']), 2)

    def test_browser_partitions_cover_every_file_once_and_balance_recorded_duration(self):
        matrix = ci_plan.browser_matrix()['include']
        groups = [json.loads(row['files']) for row in matrix]
        expected = {path.relative_to(runner.ROOT).as_posix()
                    for path in (runner.ROOT / 'frontend/e2e').rglob('*.test.ts')}
        collected = [name for group in groups for name in group]
        self.assertEqual(set(collected), expected)
        self.assertEqual(len(collected), len(set(collected)))
        self.assertTrue(all(groups))
        costs = [row['estimated_seconds'] for row in matrix]
        self.assertLess(max(costs) - min(costs), 60)

    def test_document_changes_avoid_optional_jobs_and_unknown_changes_require_them(self):
        documents = ci_plan.plan('pull_request', ['docs/TESTING.md'])
        self.assertEqual(documents['required_jobs'], ['plan', 'python', 'workflow-lint', 'frontend-ready'])
        unknown = ci_plan.plan('pull_request', ['unknown.file'])
        self.assertLessEqual({'web-e2e', 'web-bundle', 'web-vendor', 'wheel-smoke'},
                             set(unknown['required_jobs']))
        mainline = ci_plan.plan('push', ['docs/TESTING.md'])
        self.assertTrue(mainline['wide'])
        self.assertLessEqual({'web-e2e', 'web-bundle', 'web-vendor', 'wheel-build', 'wheel-smoke'},
                             set(mainline['required_jobs']))

    def test_result_gate_rejects_missing_failed_cancelled_or_required_skipped_jobs(self):
        required = ['plan', 'python', 'workflow-lint', 'frontend-ready', 'web-e2e']
        passed = {name: {'result': 'success'} for name in required}
        ci_plan.validate_results({**passed, 'web-vendor': {'result': 'skipped'}}, required)
        for result in ('failure', 'cancelled', 'skipped'):
            with self.subTest(result=result), self.assertRaises(ValueError):
                ci_plan.validate_results({**passed, 'web-e2e': {'result': result}}, required)
        with self.assertRaises(ValueError):
            ci_plan.validate_results({name: value for name, value in passed.items() if name != 'python'}, required)
        with self.assertRaises(ValueError):
            ci_plan.validate_results(passed, [])

    def test_frontend_gate_requires_only_the_selected_frontend_jobs(self):
        required = ci_plan.plan('push', ['frontend/package-lock.json'])['required_jobs']
        passed = {name: {'result': 'success'} for name in ('plan', 'web-bundle', 'web-vendor')}
        ci_plan.validate_results(passed, required, phase='frontend')
        for name in ('web-bundle', 'web-vendor'):
            for result in ('missing', 'failure', 'cancelled', 'skipped'):
                results = dict(passed)
                if result == 'missing':
                    results.pop(name)
                else:
                    results[name] = {'result': result}
                with self.subTest(name=name, result=result), self.assertRaises(ValueError):
                    ci_plan.validate_results(results, required, phase='frontend')

    def test_frontend_gate_allows_document_plans_and_rejects_an_invalid_plan(self):
        required = ci_plan.plan('pull_request', ['docs/TESTING.md'])['required_jobs']
        results = {'plan': {'result': 'success'}, 'web-bundle': {'result': 'skipped'},
                   'web-vendor': {'result': 'skipped'}}
        ci_plan.validate_results(results, required, phase='frontend')
        for result in ('failure', 'cancelled', 'skipped'):
            with self.subTest(result=result), self.assertRaises(ValueError):
                ci_plan.validate_results({**results, 'plan': {'result': result}}, required, phase='frontend')
        with self.assertRaises(ValueError):
            ci_plan.validate_results(results, [], phase='frontend')
        with self.assertRaises(ValueError):
            ci_plan.validate_results(results, required, phase='unknown')

    def test_dependency_and_manual_runs_cover_both_systems(self):
        for event, paths in [('push', ['uv.lock']), ('pull_request', ['migrations/0025.sql']),
                             ('workflow_dispatch', [])]:
            result = ci_plan.plan(event, paths)
            self.assertTrue(result['wide'])
            self.assertEqual({r['os'] for r in result['matrix']['include'] if r['scope'] == 'full'},
                             {'macos-latest', 'windows-latest'})
            wheels = result['wheel_matrix']['include']
            self.assertCountEqual(wheels, [{'os': 'windows-latest', 'python': '3.14'},
                                          {'os': 'macos-latest', 'python': '3.14'}])

    def test_pull_request_uses_changed_domains(self):
        rows = ci_plan.plan('pull_request', ['src/peach/follow_store.py'])['matrix']['include']
        self.assertTrue(any(r['scope'] == 'auto' for r in rows))
        self.assertFalse(any(r['scope'] == 'full' for r in rows))
        self.assertEqual(runner.scopes_for_changes(['scripts/localize_performer_names.py'])[0],
                         ('metadata', 'tooling'))
        self.assertEqual(runner.scopes_for_changes(['README.md'])[0], ('checks', 'tooling'))
        for document in ('docs/TESTING.md', 'docs/CLOUDDRIVE.md', 'docs/OPERATIONS.md'):
            with self.subTest(document=document):
                self.assertEqual(runner.scopes_for_changes([document])[0], ('checks',))

    def test_library_processing_selects_the_domains_that_exercise_it(self):
        """`library_processing.py` 的模块名与测试文件名对不上，按名字推不出域。

        验它的测试是 `test_metadata_library.py`（metadata 与 web 都登记）、
        `test_stale_candidates.py`（metadata）和 `test_web_e2e.py`（web）；
        映射缺了这一条，改处理任务就整轮退化成 `full`。
        """
        scopes, _ = runner.scopes_for_changes(['src/peach/library_processing.py'])
        self.assertEqual(scopes, ('metadata', 'web'))
        selected = {path.name for scope in scopes for path in runner.selected_files(scope)}
        self.assertLessEqual({'test_metadata_library.py', 'test_stale_candidates.py',
                              'test_web_e2e.py'}, selected)

    def test_missing_git_base_selects_wide_matrix(self):
        with patch.object(sys, 'argv', ['ci_plan.py', '--event', 'push', '--base', 'missing']), \
             patch.object(runner, 'changed_files', side_effect=subprocess.CalledProcessError(1, 'git')), \
             patch.dict(ci_plan.os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            ci_plan.main()
        self.assertTrue(ci_plan.json.loads(output.getvalue())['wide'])

    def test_venv_interpreter_path_keeps_environment_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            suffix = 'Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'
            executable = root / '.venv' / suffix
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.object(Path, 'resolve', side_effect=AssertionError('不能解引用 venv 解释器')):
                self.assertEqual(runner.test_evidence.interpreter(root), executable)

    def test_shards_cover_every_case_once_including_supplements(self):
        def ids(suite):
            for case in suite:
                if isinstance(case, unittest.TestSuite):
                    yield from ids(case)
                else:
                    yield case.id()
        # 使用实际域注册验证交集和补充用例，不执行其中的测试。
        serial = list(ids(runner.build_suite('follow', 'tooling')))
        shards = [list(ids(runner.build_suite('follow', 'tooling', shard_index=i, shard_count=2)))
                  for i in range(2)]
        self.assertEqual(len(serial), len(set(serial)))
        self.assertFalse(set(shards[0]) & set(shards[1]))
        self.assertCountEqual(serial, shards[0] + shards[1])

    def test_partial_run_does_not_issue_verification_record(self):
        with patch.object(runner, 'build_suite', return_value=unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])), \
             patch.object(runner.test_evidence, 'inputs', side_effect=AssertionError('分片不能签发证据')):
            self.assertEqual(runner.main(['--scope', 'full', '--shard-count', '2']), 0)

    def test_a_shard_writes_its_outcome_for_the_parent_to_merge(self):
        """分片子进程不签记录，只把成败、用例数和逐用例耗时写给父进程；域可以给多个。"""
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'shard.json'
            suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
            with patch.object(runner, 'build_suite', return_value=suite) as build, \
                 patch.object(runner.test_evidence, 'inputs', side_effect=AssertionError('分片不能签发证据')), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(['--scope', 'follow', '--scope', 'tooling',
                                              '--shard-index', '1', '--shard-count', '2',
                                              '--timings', str(report)]), 0)
            build.assert_called_once_with('follow', 'tooling', shard_index=1, shard_count=2)
            payload = json.loads(report.read_text(encoding='utf-8'))
            self.assertTrue(payload['success'])
            self.assertEqual(payload['count'], 1)
            self.assertEqual(len(payload['timings']), 1)

    def test_parallel_shards_are_merged_and_one_red_shard_fails_the_run(self):
        """父进程只汇总：每片的结论来自子进程写的文件，片数比并发多时先完成的接着领。"""
        class Done:
            def __init__(self, code):
                self.returncode = code

            def poll(self):
                return self.returncode

        def launcher(failing: int | None):
            launched = []

            def spawn(command, *, stdout, stderr, cwd):
                launched.append(command)
                index = int(command[command.index('--shard-index') + 1])
                report = Path(command[command.index('--timings') + 1])
                report.write_text(json.dumps({'success': index != failing, 'count': 10 + index,
                                              'timings': [[0.5, f'test_{index}']]}), encoding='utf-8')
                stdout.write(f'分片 {index} 的输出\n')
                return Done(0 if index != failing else 1)
            return launched, spawn

        slots = tempfile.TemporaryDirectory()
        self.addCleanup(slots.cleanup)
        launched, spawn = launcher(failing=None)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            passed, count, timings = runner.run_shards(('follow', 'tooling'), jobs=2, shard_count=3,
                                                       spawn=spawn)
        self.assertTrue(passed)
        self.assertEqual(count, 10 + 11 + 12)
        self.assertEqual(sorted(name for _, name in timings), ['test_0', 'test_1', 'test_2'])
        self.assertEqual(sorted(int(c[c.index('--shard-index') + 1]) for c in launched), [0, 1, 2])
        for command in launched:
            self.assertEqual(command[:2], [sys.executable, str(runner.ROOT / 'scripts' / 'test_runner.py')])
            self.assertEqual(command[2:6], ['--scope', 'follow', '--scope', 'tooling'])
            self.assertEqual(command[command.index('--shard-count') + 1], '3')
        for index in range(3):
            self.assertIn(f'分片 {index} 的输出', output.getvalue())

        _, spawn = launcher(failing=2)
        with contextlib.redirect_stdout(io.StringIO()):
            passed, count, _ = runner.run_shards(('checks',), jobs=4, shard_count=3,
                                                 spawn=spawn)
        self.assertFalse(passed)
        self.assertEqual(count, 10 + 11 + 12, '失败那片的用例数也要算进总数，记录里的 count 才是实跑数')

    def test_jobs_accepts_auto_or_a_positive_integer(self):
        self.assertEqual(runner.resolve_jobs('3'), 3)
        self.assertTrue(1 <= runner.resolve_jobs('auto') <= runner.DEFAULT_JOBS)
        for bad in ('0', '-1', 'many'):
            with self.subTest(bad=bad), self.assertRaises(runner.argparse.ArgumentTypeError):
                runner.resolve_jobs(bad)

    def test_a_test_reading_a_repository_file_runs_when_that_file_changes(self):
        """读了仓库里某个文件的测试，必须登记在改那个文件会选到的域里。

        域表是人手维护的，漏登记不会报错，只会在 CI 上红：`tests/test_follow_web.py` 读
        `web/app.js`，却只登记在 follow 域，于是改了 `web/app.js` 跑 `auto` 时它根本不在
        选中的文件里。这一条按测试源码里真实存在的路径拼接反查，把漏登记变成本地就红。

        判据覆盖整棵树。同一个漏洞在 `scripts/` 与 `docs/` 上一样成立：`test_babepedia_match.py`
        读 `scripts/match_babepedia_creators.py` 却只登记在 metadata 域，改那个脚本时 `auto`
        选的是 tooling。补法是 `AUTO_SCOPE_FILES` 与 `AUTO_SCOPE_PREFIXES` 让一个文件落到多个域，
        而不是把测试重复登记到不相干的域里。
        """
        for path in sorted((runner.ROOT / 'tests').glob('test_*.py')):
            read = [item for item in runner.repository_paths_read_by(path.read_text(encoding='utf-8'))
                    if not item.startswith('tests/')
                    and not any(item.startswith(prefix) for prefix in runner.FULL_ONLY_PREFIXES)]
            for item in read:
                scopes, why = runner.scopes_for_changes([item])
                selected = {chosen.name for scope in scopes for chosen in runner.selected_files(scope)}
                self.assertIn(path.name, selected, f'{path.name} 读 {item}，但 {why} 选不到它')

    def test_template_copies_have_independent_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first, second = fresh_ledger(root, 'first.db'), fresh_ledger(root, 'second.db')
            for path in (first, second):
                connection = sqlite3.connect(path)
                try:
                    self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    if path == first:
                        connection.execute('CREATE TABLE isolation_probe(value TEXT)')
                        connection.commit()
                    else:
                        self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='isolation_probe'").fetchone())
                finally:
                    connection.close()
