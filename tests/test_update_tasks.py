"""升级预检以真实临时账本、进程与文件锁验证任务保护和恢复。"""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from peach import desktop_installer, job_recovery, settings_file, update_tasks
from peach.jobs import BackgroundJob
from peach.task_runs import TaskRunStore
from support.ledger import fresh_ledger


class UpdateTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.db = self.root / 'database/ledger.db'
        self.db.parent.mkdir()
        fresh_ledger(self.db.parent)
        self.store = TaskRunStore(self.db)

    def test_restart_keeps_the_run_identity_and_committed_progress(self):
        entered, release = threading.Event(), threading.Event()
        first = BackgroundJob('resume-test', runs=self.store, task_key='feed-check')
        def initial(job_id):
            first.checkpoint(job_id, completed_sources=[1], checked=1, total=3, added=7)
            entered.set()
            release.wait(5)
        original = first.start(initial, recovery={'all': True}, initial={'checked': 0, 'total': 3})
        worker = first.thread
        self.assertTrue(entered.wait(5))
        first.stop(timeout=0)
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.store.get(original['run_id']).status, 'pending')
        second = BackgroundJob('resume-test', runs=self.store, task_key='feed-check')
        observed, processed = [], []
        def resumed(job_id):
            observed.append(second.snapshot())
            for source in (1, 2, 3):
                if source not in observed[0]['completed_sources']:
                    processed.append(source)
            second.update(job_id, checked=3, status='complete')
        result = job_recovery.resume(self.db, {'feed-check': lambda body: second.start(
            resumed, restart=True, recovery=body, initial={'checked': 0, 'total': 0})})
        second.thread.join(5)
        self.assertEqual(result, ['feed-check'])
        self.assertEqual(processed, [2, 3])
        self.assertEqual((observed[0]['job_id'], observed[0]['run_id']),
                         (original['job_id'], original['run_id']))
        self.assertEqual((observed[0]['checked'], observed[0]['total'], observed[0]['added']), (1, 3, 7))

    def test_dead_process_keeps_a_recoverable_run_pending(self):
        run = self.store.start('follow-check', trigger='manual', total=5)
        self.store.progress(run.id, current=3, total=5, throttle=0)
        job_recovery.save(self.db, 'follow-check', 'original', {'older': True}, run_id=run.id, waiting=True)
        self.assertNotIn(run.id, self.store.recover_interrupted())
        pending = self.store.get(run.id)
        self.assertEqual((pending.status, pending.progress_current, pending.progress_total), ('pending', 3, 5))

    def test_terminal_checkpoint_is_readable_by_a_new_job_instance(self):
        first = BackgroundJob('resume-test', runs=self.store, task_key='link-check')
        original = first.start(lambda identity: first.update(identity, status='complete', checked=2, total=2), recovery={})
        first.thread.join(5)
        second = BackgroundJob('resume-test', runs=self.store, task_key='link-check')
        self.assertEqual((second.snapshot()['job_id'], second.snapshot()['checked']), (original['job_id'], 2))

    def test_explicit_stop_removes_the_resume_record(self):
        entered, release = threading.Event(), threading.Event()
        job = BackgroundJob('resume-test', runs=self.store, task_key='feed-check')
        def work(identity):
            job.checkpoint(identity, checked=1)
            entered.set()
            release.wait(5)
        original = job.start(work, recovery={})
        worker = job.thread
        self.assertTrue(entered.wait(5))
        job.stop(timeout=0, resume=False)
        release.set()
        worker.join(5)
        self.assertEqual(job_recovery.read(self.db, 'feed-check'), {})
        self.assertEqual(self.store.get(original['run_id']).status, 'cancelled')

    def test_running_tasks_receive_a_pause_request_when_installation_is_accepted(self):
        for trigger in ('manual', 'scheduled'):
            with self.subTest(trigger=trigger):
                run = TaskRunStore(self.db).start('library-processing', trigger=trigger)
                token = update_tasks.reserve(self.db)
                self.assertTrue(update_tasks.is_pausing(self.db))
                self.assertFalse(update_tasks.wait_quiescent(self.db, timeout=0))
                self.store.finish(run.id, 'succeeded')
                self.assertTrue(update_tasks.wait_quiescent(self.db, timeout=0))
                update_tasks.release(self.db, token)

    def test_reservation_prevents_task_start_until_release(self):
        token = update_tasks.reserve(self.db)
        with self.assertRaises(update_tasks.UpdateBusy):
            self.store.start('timeline-thumbnails', trigger='manual')
        job = BackgroundJob('test', runs=self.store, task_key='library-processing')
        called = threading.Event()
        state = job.start(lambda _: called.set(), recovery={'stage': 'previews'})
        self.assertFalse(called.is_set())
        self.assertTrue(state['waiting_for_update'])
        self.assertEqual(job_recovery.read(self.db, 'library-processing')['request'], {'stage': 'previews'})
        update_tasks.release(self.db, token)
        self.assertIsNotNone(self.store.start('timeline-thumbnails', trigger='manual'))

    def test_checkpoint_pause_and_resume_keep_the_same_task_run(self):
        entered, progress = threading.Event(), threading.Event()
        job = BackgroundJob('test', runs=self.store, task_key='library-processing')
        attempts = []
        def work(job_id):
            attempts.append(job_id)
            entered.set()
            progress.wait(5)
            job.update(job_id, status='complete')
        job.start(work, recovery={'stage': 'previews'})
        self.assertTrue(entered.wait(5))
        run_id = job.snapshot()['run_id']
        token = update_tasks.reserve(self.db)
        progress.set()
        job.thread.join(5)
        self.assertFalse(job.thread.is_alive())
        self.assertTrue(update_tasks.wait_quiescent(self.db, timeout=0))
        self.assertEqual(self.store.get(run_id).status, 'pending')
        self.assertEqual(self.store.recover_interrupted(), [])
        self.assertTrue(job.snapshot()['waiting_for_update'])
        update_tasks.release(self.db, token)
        resumed = job_recovery.resume(self.db, {'library-processing':
            lambda body: job.start(work, restart=True, recovery=body)})
        self.assertEqual(resumed, ['library-processing'])
        job.thread.join(5)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(job.snapshot()['run_id'], run_id)
        self.assertEqual(self.store.get(run_id).status, 'succeeded')

    def test_installation_pause_is_reconciled_when_the_service_exits_during_a_step(self):
        run = self.store.start('library-processing', trigger='manual', pid=-1)
        job_recovery.save(self.db, 'library-processing', 'old', {'stage': 'previews'}, run_id=run.id)
        token = update_tasks.reserve(self.db)
        path = update_tasks.state_for(self.db).parent / 'job-recovery/library-processing.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['pid'] = -1
        path.write_text(json.dumps(data), encoding='utf-8')
        job_recovery.reconcile_paused(self.db)
        self.assertEqual(self.store.get(run.id).status, 'pending')
        self.assertEqual(self.store.recover_interrupted(), [])
        update_tasks.release(self.db, token)

    def test_service_shutdown_during_installation_preserves_the_run_until_its_worker_exits(self):
        entered, release = threading.Event(), threading.Event()
        job = BackgroundJob('test', runs=self.store, task_key='library-processing')
        def work(job_id):
            entered.set()
            release.wait(10)
            job.update(job_id, status='complete')
        state = job.start(work, recovery={'stage': 'previews'})
        thread = job.thread
        token = None
        try:
            self.assertTrue(entered.wait(5))
            token = update_tasks.reserve(self.db)
            job.stop(timeout=0)
            self.assertEqual(self.store.get(state['run_id']).status, 'pending')
            update_tasks.release(self.db, token)
            handler = {'library-processing': lambda body: job.start(work, restart=True, recovery=body)}
            self.assertEqual(job_recovery.resume(self.db, handler), [])
        finally:
            release.set()
            thread.join(5)
            if token is not None:
                update_tasks.release(self.db, token)
        self.assertFalse(thread.is_alive())
        self.assertEqual(job_recovery.resume(self.db, handler), ['library-processing'])
        job.thread.join(5)
        self.assertEqual(job.snapshot()['job_id'], state['job_id'])
        self.assertEqual(job.snapshot()['run_id'], state['run_id'])
        self.assertEqual(self.store.get(state['run_id']).status, 'succeeded')

    def test_another_service_cannot_replace_the_scope_already_waiting_for_installation(self):
        token = update_tasks.reserve(self.db)
        first = BackgroundJob('one', runs=self.store, task_key='library-processing')
        second = BackgroundJob('two', runs=TaskRunStore(self.db), task_key='library-processing')
        original = first.start(lambda _: None, recovery={'stage': 'previews', 'asset_ids': [7]})
        later = second.start(lambda _: None, recovery={'stage': 'metadata', 'asset_ids': [9]})
        self.assertEqual(original['job_id'], later['job_id'])
        self.assertEqual(job_recovery.read(self.db, 'library-processing')['request'],
                         {'stage': 'previews', 'asset_ids': [7]})
        update_tasks.release(self.db, token)

    def test_source_batch_waits_for_installation_and_continues_in_the_same_process(self):
        import argparse
        from peach.jobs import DiskGuard, job_main
        entered, checkpoint = threading.Event(), threading.Event()
        lock = self.root / 'state/.probe.lock'
        parser = argparse.ArgumentParser()
        parser.add_argument('--lock', default=lock, type=Path)
        parser.add_argument('--db', default=self.db, type=Path)
        attempts, results = [], []
        def run(_args):
            guard = DiskGuard(self.root, 0)
            attempts.append(os.getpid())
            entered.set()
            checkpoint.wait(5)
            guard.check()
            return 0
        thread = threading.Thread(target=lambda: results.append(job_main(lambda: parser, run, [])))
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            token = update_tasks.reserve(self.db)
            checkpoint.set()
            self.assertTrue(update_tasks.wait_quiescent(self.db, timeout=5))
            self.assertTrue(thread.is_alive())
            update_tasks.release(self.db, token)
        finally:
            checkpoint.set()
            update_tasks.release_owner(self.db, os.getpid())
            thread.join(10)
        self.assertEqual(results, [0])
        self.assertEqual(attempts, [os.getpid(), os.getpid()])

    def test_activity_registration_remains_readable_until_the_protected_scan_finishes(self):
        entered, finish, withdrawing = (threading.Event() for _ in range(3))
        errors = []
        mutex, read = update_tasks._mutex, update_tasks._read

        def work():
            try:
                with update_tasks.activity(self.db, '受保护任务'):
                    entered.set()
                    if not finish.wait(5):
                        raise TimeoutError('任务结束信号未取得')
            except Exception as error:
                errors.append(error)
            finally:
                withdrawing.set()

        worker = threading.Thread(target=work)

        @contextmanager
        def observed_mutex(root):
            lock = mutex(root)
            if threading.current_thread() is worker and finish.is_set():
                withdrawing.set()
            with lock:
                yield

        def protected_read(path):
            if path.parent.name == 'active':
                finish.set()
                self.assertTrue(withdrawing.wait(5))
                self.assertTrue(path.is_file(), '受保护扫描期间任务登记必须保留')
            return read(path)

        with patch.object(update_tasks, '_mutex', observed_mutex), patch.object(
                update_tasks, '_read', protected_read):
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(update_tasks._running(self.db), ['受保护任务'])
            finally:
                finish.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(update_tasks.wait_quiescent(self.db, timeout=0))

    def test_a_live_thread_remains_protected_after_its_record_is_cancelled(self):
        entered, release = threading.Event(), threading.Event()
        job = BackgroundJob('test', runs=self.store, task_key='library-processing')
        def work(_):
            entered.set()
            release.wait(10)
        job.start(work)
        thread = job.thread
        try:
            self.assertTrue(entered.wait(5))
            job.stop(timeout=0)
            token = update_tasks.reserve(self.db)
            self.assertFalse(update_tasks.wait_quiescent(self.db, timeout=0))
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(update_tasks.wait_quiescent(self.db, timeout=0))
        update_tasks.release(self.db, token)

    def child(self, source):
        child = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', source, str(self.db)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding='utf-8')
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child

    def test_cli_activity_is_observed_without_refusing_the_installation_request(self):
        child = self.child("from pathlib import Path; import sys; "
                           "from peach.update_tasks import register; "
                           "register(Path(sys.argv[1]), 'CLI 抽帧'); print('ready', flush=True); input()")
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        token = update_tasks.reserve(self.db)
        self.assertFalse(update_tasks.wait_quiescent(self.db, timeout=0))
        child.kill()
        child.communicate(timeout=10)
        self.assertTrue(update_tasks.wait_quiescent(self.db, timeout=0))
        update_tasks.release(self.db, token)

    def test_installer_accepts_a_running_task_and_holds_the_pause_until_completion(self):
        config = settings_file.load_config(environ={'PEACH_DATA_ROOT': str(self.root)})
        self.store.start('library-processing', trigger='manual')
        error = self.root / 'prepare-error.txt'
        with patch.object(settings_file, 'load_config', return_value=config), patch.object(
                desktop_installer, 'stop', return_value=True) as stop, patch.object(
                update_tasks, 'wait_quiescent', return_value=False):
            self.assertEqual(desktop_installer.prepare(self.root / 'app', os.getpid(), error), 0)
            stop.assert_called_once()
            self.assertFalse(error.exists())
            with self.assertRaises(update_tasks.UpdateBusy):
                self.store.start('timeline-thumbnails', trigger='manual')
            desktop_installer.finish_install(self.root / 'app', os.getpid())
            self.assertIsNotNone(self.store.start('timeline-thumbnails', trigger='manual'))

    def test_unreadable_task_state_keeps_the_database_bytes_for_the_installer_diagnostic(self):
        self.db.write_bytes(b'not sqlite')
        token = update_tasks.reserve(self.db)
        self.assertFalse(update_tasks.wait_quiescent(self.db, timeout=0))
        self.assertEqual(self.db.read_bytes(), b'not sqlite')
        update_tasks.release(self.db, token)

    def test_installer_migration_uses_the_selected_data_directory_and_skips_empty_installs(self):
        config = settings_file.load_config(environ={'PEACH_DATA_ROOT': str(self.root)})
        with patch.object(settings_file, 'load_config', return_value=config), patch(
                'peach.cli.main', return_value=0) as migrate:
            self.assertEqual(desktop_installer.migrate_install(self.root / 'app'), 0)
            migrate.assert_called_once_with(['migrate', 'upgrade', '--yes', '--db', str(self.db)])
            migrate.reset_mock()
            self.db.unlink()
            self.assertEqual(desktop_installer.migrate_install(self.root / 'app'), 0)
            migrate.assert_not_called()
            self.assertFalse(self.db.exists())

    def test_installer_owner_handoff_does_not_allow_new_tasks_between_processes(self):
        token = update_tasks.reserve(self.db)
        child = self.child('print("ready", flush=True); input()')
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        update_tasks.transfer(self.db, token, child.pid)
        update_tasks.release(self.db, 'wrong-token')
        with self.assertRaises(update_tasks.UpdateBusy):
            self.store.start('library-processing', trigger='manual')
        child.kill()
        child.communicate(timeout=10)
        self.assertIsNotNone(self.store.start('library-processing', trigger='manual'))

    def test_interrupted_task_request_is_replayed_once_by_two_service_instances(self):
        child = self.child("from pathlib import Path; import sys; from peach.job_recovery import save; "
                           "save(Path(sys.argv[1]), 'library-processing', 'old', "
                           "{'stage':'previews','asset_ids':[7,9]}); print('ready', flush=True); input()")
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        calls = []
        def replay(body):
            calls.append(body)
            job_recovery.save(self.db, 'library-processing', 'new', body)
        handlers = {'library-processing': replay}
        self.assertEqual(job_recovery.resume(self.db, handlers), [])
        child.kill()
        child.communicate(timeout=10)
        self.assertEqual(job_recovery.resume(self.db, handlers), ['library-processing'])
        self.assertEqual(job_recovery.resume(self.db, handlers), [])
        self.assertEqual(calls, [{'stage': 'previews', 'asset_ids': [7, 9]}])

    def test_completed_failed_and_explicitly_cancelled_requests_are_not_replayed(self):
        for status in ('complete', 'failed'):
            job_recovery.save(self.db, 'library-processing', 'a', {'stage': 'collect'})
            job_recovery.finish(self.db, 'library-processing', 'a', status)
            self.assertEqual(job_recovery.read(self.db, 'library-processing')['status'], status)
        job_recovery.cancel(self.db, 'library-processing')
        self.assertEqual(job_recovery.read(self.db, 'library-processing'), {})

    def test_a_stale_request_validation_failure_is_visible_without_an_automatic_retry_loop(self):
        job_recovery.save(self.db, 'library-processing', 'old', {'asset_ids': [404]})
        path = update_tasks.state_for(self.db).parent / 'job-recovery/library-processing.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['pid'] = -1
        path.write_text(json.dumps(data), encoding='utf-8')
        calls = []
        def fail(body):
            calls.append(body)
            raise ValueError('影片已移出馆藏')
        with self.assertLogs('peach.job_recovery', level='WARNING'):
            self.assertEqual(job_recovery.resume(self.db, {'library-processing': fail}), [])
        self.assertEqual(job_recovery.read(self.db, 'library-processing')['status'], 'blocked')
        self.assertEqual(job_recovery.resume(self.db, {'library-processing': fail}), [])
        self.assertEqual(len(calls), 1)
