"""馆藏处理控制与启动粗估的隔离行为回归。"""
import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from peach import job_recovery
from peach.processing_control import (CURRENT, ProcessingControl, ProcessingEstimate,
                                      checkpoint, control_for, initial_estimate, user_paused, deadline_after)
from peach.settings_file import PeachConfig
from peach.web_library_processing import w_library_processing
from support.ledger import fresh_ledger


class ProcessingControlTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                                  locations={'local': ('R:\\Media',)})
        self.db = fresh_ledger(self.root)

    def seed(self, count=10):
        import sqlite3
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for index in range(count):
                connection.execute('INSERT INTO asset(location,path,name,medium,size) VALUES(?,?,?,?,?)',
                                   ('local', f'R:\\Media\\{index}.mp4', f'{index}.mp4', 'video', 1024**3))

    def test_start_estimate_uses_known_count_size_and_scoped_selection(self):
        self.seed()
        estimate = initial_estimate(self.config, self.db, 'all')
        chosen = initial_estimate(self.config, self.db, 'all', [1, 2])
        low = initial_estimate(self.config, self.db, 'all', mode='low')
        high = initial_estimate(self.config, self.db, 'all', mode='high')
        self.assertEqual(estimate['estimate_file_count'], 10)
        self.assertEqual(estimate['estimate_bytes'], 10 * 1024**3)
        self.assertGreater(estimate['eta_seconds_max'], estimate['eta_seconds_min'])
        self.assertLess(chosen['eta_seconds_max'], estimate['eta_seconds_max'])
        self.assertGreater(low['eta_seconds_max'], estimate['eta_seconds_max'])
        self.assertLess(high['eta_seconds_max'], estimate['eta_seconds_max'])
        self.assertTrue(estimate['eta_initial'])
        self.assertIn('未含未知新增', estimate['eta_scope'])

    def test_new_directory_reports_counting_without_invented_time(self):
        estimate = initial_estimate(self.config, self.db, 'scan')
        self.assertIsNone(estimate['eta_seconds_min'])
        self.assertIsNone(estimate['eta_seconds_max'])
        self.assertIn('统计', estimate['eta_basis'])

    def test_progress_calibrates_range_and_paused_time_does_not_count(self):
        self.seed()
        state = dict(initial_estimate(self.config, self.db, 'metadata'),
                     requested_stage='metadata', status='running', stage='读取本地资料', checked=0, total=10)
        with patch('peach.processing_control.time.monotonic', return_value=100):
            estimate = ProcessingEstimate(self.config, state, None)
            estimate.update(state)
        state['checked'] = 2
        with patch('peach.processing_control.time.monotonic', return_value=110):
            estimate.update(state)
        self.assertEqual(state['eta_kind'], 'measured')
        self.assertFalse(state['eta_initial'])
        self.assertLessEqual(state['eta_seconds_min'], 40)
        self.assertGreaterEqual(state['eta_seconds_max'], 40)
        frozen = (state['eta_seconds_min'], state['eta_seconds_max'])
        state['status'] = 'paused'
        with patch('peach.processing_control.time.monotonic', return_value=9999):
            estimate.update(state)
        self.assertEqual((state['eta_seconds_min'], state['eta_seconds_max']), frozen)

    def test_metadata_passes_use_independent_measured_samples(self):
        self.seed()
        state = dict(initial_estimate(self.config, self.db, 'metadata'),
                     requested_stage='metadata', status='running', stage='读取本地资料', checked=0, total=10)
        with patch('peach.processing_control.time.monotonic', return_value=100):
            estimate = ProcessingEstimate(self.config, state, None)
            estimate.update(state)
        state['checked'] = 3
        with patch('peach.processing_control.time.monotonic', return_value=103):
            estimate.update(state)
        self.assertEqual(state['eta_kind'], 'measured')
        state.update(stage='采集缺失资料', checked=0)
        with patch('peach.processing_control.time.monotonic', return_value=200):
            estimate.update(state)
        self.assertTrue(state['eta_initial'])
        self.assertEqual(state['eta_kind'], 'estimating')
        self.assertEqual(state['eta_scope'], '整趟任务')
        state['checked'] = 2
        with patch('peach.processing_control.time.monotonic', return_value=204):
            estimate.update(state)
        self.assertLessEqual(state['eta_seconds_min'], 16)
        self.assertGreaterEqual(state['eta_seconds_max'], 16)
        self.assertLess(state['eta_seconds_max'], 40)

    def test_total_estimate_includes_pending_media_and_collection(self):
        state = dict(speed_mode='standard', status='running', requested_stage='all',
                     stage='读取本地资料', checked=0, total=10,
                     estimate_pending={'local': 10, 'probe': 2, 'previews': 3, 'metadata': 10})
        with patch('peach.processing_control.time.monotonic', return_value=100):
            estimate = ProcessingEstimate(self.config, state, None)
            estimate.update(state)
        state['checked'] = 8
        with patch('peach.processing_control.time.monotonic', return_value=108):
            estimate.update(state)
        self.assertEqual(state['eta_scope'], '整趟任务')
        self.assertTrue(state['eta_initial'])
        self.assertGreater(state['eta_seconds_min'], 40)
        state.update(checked=0, total=2, stage='采集缺失资料', estimate_pending={'metadata': 2})
        with patch('peach.processing_control.time.monotonic', return_value=120):
            estimate.update(state)
        self.assertLessEqual(state['eta_seconds_max'], 40)

    def test_unmeasured_finalization_does_not_keep_a_completed_phase_estimate(self):
        state = dict(speed_mode='standard', status='running', requested_stage='metadata',
                     stage='保存待确认资料', checked=10, total=10, estimate_pending={},
                     eta_seconds_min=1, eta_seconds_max=2)
        estimate = ProcessingEstimate(self.config, state, None)
        estimate.update(state)
        self.assertIsNone(state['eta_seconds_max'])

    def test_slow_tail_uses_recent_completions_instead_of_the_fast_prefix(self):
        state = dict(speed_mode='standard', status='running', requested_stage='metadata',
                     stage='读取本地资料', checked=0, total=120, estimate_pending={'local': 120})
        with patch('peach.processing_control.time.monotonic', return_value=100):
            estimate = ProcessingEstimate(self.config, state, None)
            estimate.update(state)
        for done in range(1, 101):
            state['checked'] = done
            elapsed = done if done <= 80 else 80 + (done - 80) * 10
            with patch('peach.processing_control.time.monotonic', return_value=100 + elapsed):
                estimate.update(state)
        self.assertGreaterEqual(state['eta_seconds_min'], 150)
        self.assertGreaterEqual(state['eta_seconds_max'], 240)

    def test_progress_count_rewind_starts_a_new_sample_for_same_stage(self):
        self.seed()
        state = dict(initial_estimate(self.config, self.db, 'metadata'),
                     requested_stage='metadata', status='running', stage='读取时长与分辨率', checked=0, total=10)
        with patch('peach.processing_control.time.monotonic', return_value=100):
            estimate = ProcessingEstimate(self.config, state, None)
            estimate.update(state)
        state['checked'] = 5
        with patch('peach.processing_control.time.monotonic', return_value=150):
            estimate.update(state)
        state['checked'] = 0
        with patch('peach.processing_control.time.monotonic', return_value=200):
            estimate.update(state)
        self.assertEqual(state['eta_kind'], 'estimating')
        state['checked'] = 2
        with patch('peach.processing_control.time.monotonic', return_value=202):
            estimate.update(state)
        self.assertLessEqual(state['eta_seconds_min'], 8)
        self.assertGreaterEqual(state['eta_seconds_max'], 8)
        self.assertLess(state['eta_seconds_max'], 20)

    def test_pause_waits_at_checkpoint_and_resume_releases_same_job(self):
        control = control_for(self.config)
        entered, paused, finished = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def publish(**values):
            if values['status'] == 'paused':
                paused.set()
        def worker():
            try:
                with control.bind('one', publish=publish, active=lambda: True, db_path=self.db, mode='high'):
                    entered.set()
                    control.request('pause', 'one')
                    checkpoint('probe')
                    finished.set()
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        self.addCleanup(lambda: control.request('resume', 'one'))
        self.addCleanup(lambda: thread.join(2))
        self.assertTrue(entered.wait(2))
        self.assertTrue(paused.wait(2))
        self.assertFalse(finished.is_set())
        self.assertTrue(user_paused(self.db, 'one'))
        control.request('resume', 'one')
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertFalse(user_paused(self.db, 'one'))

    def test_saved_pause_intent_prevents_automatic_restart(self):
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='high'):
            control.request('pause', 'one')
        restored = ProcessingControl(control.path)
        self.assertTrue(restored.paused)
        self.assertEqual(restored.mode, 'high')
        job_recovery.save(self.db, 'library-processing', 'one', {'stage': 'scan'}, waiting=True)
        handler = Mock()
        self.assertEqual(job_recovery.resume(self.db, {'library-processing': handler}), [])
        handler.assert_not_called()

    def test_low_mode_leaves_io_gaps_and_preserves_unrelated_context(self):
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='low'), \
                patch('peach.processing_control.time.monotonic', return_value=10), \
                patch.object(control.event, 'wait', side_effect=lambda _: control.request('set_speed', mode='standard')) as wait:
            checkpoint('network')
            checkpoint('network')
            wait.assert_called_once()
        self.assertIsNone(CURRENT.get())
        checkpoint('network')
        restored = ProcessingControl(control.path)
        self.assertEqual(restored.mode, 'standard')

    def test_stale_job_control_is_rejected(self):
        contract = SimpleNamespace(library_processing_job=Mock())
        contract.library_processing_job.snapshot.return_value = {'job_id': 'current', 'status': 'running'}
        with patch('peach.web_library_processing.settings_file.active', return_value=self.config):
            with self.assertRaisesRegex(ValueError, '任务已变化'):
                w_library_processing(contract, {'action': 'pause', 'job_id': 'stale'})
            with self.assertRaisesRegex(ValueError, '任务已变化'):
                w_library_processing(contract, {'action': 'set_speed', 'job_id': 'stale', 'speed_mode': 'low'})

    def test_completed_timing_is_available_for_next_initial_estimate(self):
        self.seed()
        state = dict(initial_estimate(self.config, self.db, 'metadata'),
                     requested_stage='metadata', status='running')
        with patch('peach.processing_control.time.monotonic', return_value=100):
            estimate = ProcessingEstimate(self.config, state, None)
        state['status'] = 'complete'
        with patch('peach.processing_control.time.monotonic', return_value=200):
            estimate.update(state)
        next_run = initial_estimate(self.config, self.db, 'metadata')
        self.assertEqual(next_run['eta_kind'], 'historical')
        self.assertTrue(next_run['eta_initial'])
        self.assertEqual(json.loads((self.config.directory('state') / 'library-processing-timing.json').read_text()),
                         {'metadata': 10.0})

    def test_action_deadline_keeps_the_same_budget_after_user_pause(self):
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db), \
                patch('peach.processing_control.time.monotonic', return_value=100):
            deadline = deadline_after(90)
            self.assertEqual(deadline - 110, 80)
            control.paused_seconds += 3600
            self.assertEqual(deadline - 3710, 80)
            self.assertEqual((deadline + 5) - 3710, 85)
            self.assertTrue(3710 < deadline)
            self.assertTrue(3800 > deadline)

    def test_web_pause_and_resume_preserve_running_job_and_initial_estimate(self):
        from peach.jobs import BackgroundJob
        from peach.library_processing import _save, state_path
        from peach.web_library_processing import q_library_processing
        from peach.repository import LedgerDatabase
        from peach.task_runs import TaskRunStore
        database_root = self.config.directory('database')
        database_root.mkdir(parents=True)
        db = fresh_ledger(database_root)
        job = BackgroundJob('library-processing', runs=TaskRunStore(LedgerDatabase(db)))
        contract = SimpleNamespace(db_path=db, library_processing_job=job,
                                   candidate_root=self.root / 'candidates', cover_root=self.root / 'covers',
                                   database=job.runs.database, cache_bust=lambda: None)
        entered, advance, paused, done = (threading.Event() for _ in range(4))
        calls, errors = [], []
        def process(*_args, **kwargs):
            identity = kwargs['job_id']
            calls.append(identity)
            def report(**values):
                kwargs['report'](values)
                if values.get('status') == 'paused':
                    paused.set()
            try:
                with control_for(self.config).bind(identity, publish=report, active=lambda: True, db_path=db):
                    _save(state_path(self.config), dict(job_id=identity, status='running', requested_stage='metadata'))
                    entered.set()
                    advance.wait(2)
                    checkpoint()
                    done.set()
                    return {'status': 'complete'}
            except BaseException as exc:
                errors.append(exc)
                raise
        with patch('peach.web_library_processing.settings_file.active', return_value=self.config), \
                patch('peach.web_library_processing.process_library', side_effect=process):
            initial = w_library_processing(contract, {'stage': 'metadata', 'speed_mode': 'high'})
            self.assertIn('eta_seconds_max', initial)
            self.assertEqual(initial['speed_mode'], 'high')
            self.assertTrue(entered.wait(2))
            run_id = initial['run_id']
            identity = initial['job_id']
            reply = w_library_processing(contract, {'action': 'pause', 'job_id': identity})
            self.assertEqual(reply['status'], 'pausing')
            advance.set()
            self.assertTrue(paused.wait(2))
            self.assertEqual(q_library_processing(contract, {})['status'], 'paused')
            self.assertFalse(done.is_set())
            w_library_processing(contract, {'action': 'resume', 'job_id': identity})
            job.thread.join(2)
            self.assertTrue(done.is_set())
            self.assertEqual(job.snapshot()['status'], 'complete')
            self.assertEqual(job.snapshot()['run_id'], run_id)
            self.assertEqual(calls, [identity])
            self.assertEqual(errors, [])
            self.assertEqual(job.runs.get(run_id).status, 'succeeded')

    def test_low_mode_ffmpeg_uses_single_decode_filter_and_encode_threads(self):
        from peach.frame_capture import capture_frame
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='low'), \
                patch('peach.frame_capture._readable_image', return_value=True), \
                patch('peach.frame_capture.subprocess.run', return_value=SimpleNamespace(returncode=0, stderr=b'')) as run:
            capture_frame('ffmpeg', 'video.mp4', 1, self.root / 'frame.jpg')
        command = run.call_args.args[0]
        self.assertEqual(command.count('-threads'), 2)
        self.assertEqual(command[command.index('-filter_threads') + 1], '1')

    def test_user_paused_job_keeps_identity_and_progress_through_service_restart(self):
        from peach.jobs import BackgroundJob
        from peach.library_processing import _save, state_path
        from peach.repository import LedgerDatabase
        from peach.task_runs import TaskRunStore
        database_root = self.config.directory('database')
        database_root.mkdir(parents=True)
        db = fresh_ledger(database_root)
        runs = TaskRunStore(LedgerDatabase(db))
        first = BackgroundJob('library-processing', runs=runs)
        contract = SimpleNamespace(db_path=db, library_processing_job=first,
                                   candidate_root=self.root / 'candidates', cover_root=self.root / 'covers',
                                   database=runs.database, cache_bust=lambda: None)
        entered, advance, paused = (threading.Event() for _ in range(3))
        identities, resumes = [], []
        def process(*_args, **kwargs):
            identity = kwargs['job_id']
            identities.append(identity)
            resumes.append(kwargs['resume_state'])
            state = dict(job_id=identity, requested_stage='metadata', status='running', completed_metadata=['1:R:\\a.mp4'])
            def report(**values):
                values.pop('resumed_after', None)
                state.update(values)
                _save(state_path(self.config), state)
                kwargs['report'](dict(state))
                if state['status'] == 'paused':
                    paused.set()
            with control_for(self.config).bind(identity, publish=report, active=kwargs['active'], db_path=db):
                _save(state_path(self.config), state)
                if len(identities) == 1:
                    entered.set()
                    advance.wait(2)
                checkpoint()
                return {'status': 'complete'}
        with patch('peach.web_library_processing.settings_file.active', return_value=self.config), \
                patch('peach.web_library_processing.process_library', side_effect=process):
            initial = w_library_processing(contract, {'stage': 'metadata', 'speed_mode': 'high'})
            self.assertTrue(entered.wait(2))
            w_library_processing(contract, {'action': 'pause', 'job_id': initial['job_id']})
            advance.set()
            self.assertTrue(paused.wait(2))
            first.stop()
            self.assertEqual(runs.get(initial['run_id']).status, 'pending')
            self.assertEqual(runs.get(initial['run_id']).progress_label, '已暂停，等待继续')
            second = BackgroundJob('library-processing', runs=runs)
            contract.library_processing_job = second
            w_library_processing(contract, {'action': 'resume', 'job_id': initial['job_id']})
            second.thread.join(2)
            self.assertEqual(identities, [initial['job_id'], initial['job_id']])
            self.assertEqual(resumes[1]['completed_metadata'], ['1:R:\\a.mp4'])
            self.assertEqual(second.snapshot()['status'], 'complete')
            self.assertEqual(second.snapshot()['run_id'], initial['run_id'])
            self.assertEqual(control_for(self.config).mode, 'high')
            self.assertEqual(runs.get(initial['run_id']).status, 'succeeded')

    def test_three_intensities_set_distinct_io_start_intervals(self):
        control = control_for(self.config)
        intervals = {}
        for mode in ('low', 'standard', 'high'):
            with self.subTest(mode=mode), \
                    control.bind(mode, publish=lambda **_: None, active=lambda: True, db_path=self.db, mode=mode), \
                    patch('peach.processing_control.time.monotonic', return_value=10):
                checkpoint('network')
                intervals[mode] = control.next_work['network'] - 10
        self.assertGreater(intervals['low'], intervals['standard'])
        self.assertGreater(intervals['standard'], intervals['high'])
        self.assertEqual(intervals['high'], 0)

    def test_three_intensities_set_decode_filter_and_encode_threads(self):
        from peach.frame_capture import capture_frame, tile_frames
        control = control_for(self.config)
        for mode, threads in (('low', '1'), ('standard', '2'), ('high', None)):
            with self.subTest(mode=mode), \
                    control.bind(mode, publish=lambda **_: None, active=lambda: True, db_path=self.db, mode=mode), \
                    patch('peach.frame_capture.checkpoint'), \
                    patch('peach.frame_capture._readable_image', return_value=True), \
                    patch('peach.frame_capture.subprocess.run', return_value=SimpleNamespace(returncode=0, stderr=b'')) as run:
                capture_frame('ffmpeg', 'video.mp4', 1, self.root / 'frame.jpg')
                tile_frames('ffmpeg', 'frame%02d.jpg', 3, 3, self.root / 'tile.jpg')
                for call, filter_option in zip(run.call_args_list, ('-filter_threads', '-filter_complex_threads')):
                    command = call.args[0]
                    if threads is None:
                        self.assertNotIn('-threads', command)
                        self.assertNotIn(filter_option, command)
                    else:
                        self.assertEqual(command.count('-threads'), 2)
                        self.assertEqual(command[command.index(filter_option) + 1], threads)
                        self.assertEqual([command[index + 1] for index, value in enumerate(command)
                                          if value == '-threads'], [threads, threads])

    def test_intensity_switch_recalibrates_estimate_in_every_direction(self):
        self.seed()
        control = control_for(self.config)
        for initial, target in (('low', 'standard'), ('standard', 'high'), ('high', 'low')):
            with self.subTest(initial=initial, target=target), \
                    control.bind(initial, publish=lambda **_: None, active=lambda: True,
                                 db_path=self.db, mode=initial):
                state = dict(initial_estimate(self.config, self.db, 'metadata', mode=initial),
                             status='paused', requested_stage='metadata')
                estimate = ProcessingEstimate(self.config, state, control)
                old = state['eta_seconds_max']
                control.request('set_speed', mode=target)
                estimate.update(state)
                self.assertEqual(state['speed_mode'], target)
                if target == 'low':
                    self.assertGreater(state['eta_seconds_max'], old)
                else:
                    self.assertLess(state['eta_seconds_max'], old)

    def test_high_intensity_honors_installation_pause(self):
        from peach.jobs import JobPaused
        from peach import update_tasks
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='high'):
            token = update_tasks.reserve(self.db)
            try:
                with self.assertRaises(JobPaused):
                    checkpoint('frame')
            finally:
                update_tasks.release(self.db, token)
            checkpoint('frame')
            self.assertEqual(control.mode, 'high')

    def test_high_history_is_normalized_for_every_initial_intensity(self):
        self.seed()
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='high'):
            state = dict(initial_estimate(self.config, self.db, 'metadata', mode='high'),
                         requested_stage='metadata', status='running')
            with patch('peach.processing_control.time.monotonic', return_value=100):
                estimate = ProcessingEstimate(self.config, state, control)
            state['status'] = 'complete'
            with patch('peach.processing_control.time.monotonic', return_value=175):
                estimate.update(state)
        history = json.loads((self.config.directory('state') / 'library-processing-timing.json').read_text())
        self.assertEqual(history['metadata'], 10.0)
        low, standard, high = [initial_estimate(self.config, self.db, 'metadata', mode=mode)
                               for mode in ('low', 'standard', 'high')]
        self.assertEqual(high['eta_kind'], 'historical')
        self.assertGreater(low['eta_seconds_max'], standard['eta_seconds_max'])
        self.assertGreater(standard['eta_seconds_max'], high['eta_seconds_max'])

    def test_high_intensity_and_pause_intent_are_shared_between_control_instances(self):
        control = control_for(self.config)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='standard'):
            external = ProcessingControl(control.path)
            external.request('set_speed', 'one', mode='high')
            external.request('pause', 'one')
            control.refresh(force=True)
            self.assertEqual(control.mode, 'high')
            self.assertTrue(control.paused)
            external.request('resume', 'one')
            control.refresh(force=True)
            self.assertFalse(control.paused)
            self.assertEqual(control.mode, 'high')

    def test_api_switches_between_all_intensities_with_proportional_paused_estimates(self):
        from peach.jobs import BackgroundJob
        control = control_for(self.config)
        job = BackgroundJob('library-processing')
        job.state = dict(job_id='one', status='paused', speed_mode='low', eta_seconds_min=180, eta_seconds_max=360)
        contract = SimpleNamespace(db_path=self.db, library_processing_job=job)
        with control.bind('one', publish=lambda **_: None, active=lambda: True, db_path=self.db, mode='low'), \
                patch('peach.web_library_processing.settings_file.active', return_value=self.config):
            control.request('pause', 'one')
            high = w_library_processing(contract, {'action': 'set_speed', 'job_id': 'one', 'speed_mode': 'high'})
            self.assertEqual((high['speed_mode'], high['status']), ('high', 'paused'))
            self.assertEqual((high['eta_seconds_min'], high['eta_seconds_max']), (75, 150))
            standard = w_library_processing(contract, {'action': 'set_speed', 'job_id': 'one', 'speed_mode': 'standard'})
            self.assertEqual((standard['eta_seconds_min'], standard['eta_seconds_max']), (100, 200))
            low = w_library_processing(contract, {'action': 'set_speed', 'job_id': 'one', 'speed_mode': 'low'})
            self.assertEqual((low['eta_seconds_min'], low['eta_seconds_max']), (180, 360))
            self.assertTrue(control.paused)

    def test_scan_progress_accumulates_discovered_counts_across_roots(self):
        from peach.library_processing import _scan_sources
        reports = []
        config = PeachConfig(self.root, self.root / 'config.toml', present=True,
                             locations={'local': ('R:\\Media', 'R:\\Other')})
        def scan(*_args, **kwargs):
            kwargs['progress'](1, 10)
            kwargs['progress'](2, 30)
            return SimpleNamespace(files=2, total_bytes=30, new_ids=[])
        with patch('peach.library_processing.scan_location', side_effect=scan), \
                patch('peach.library_processing._online_source_root', return_value=self.root), \
                patch('peach.library_processing._ffprobe_path', return_value=None), \
                patch('peach.library_processing.probe_unmeasured', return_value=0), \
                patch('peach.library_processing.reattach_in'):
            result = _scan_sources(config, self.db, locations=config.locations, asset_ids=None,
                                   stage='scan', guard=Mock(), update=lambda **values: reports.append(values),
                                   issue=lambda *_args, **_kwargs: None)
        discovering = [row for row in reports if row.get('current_action') == 'scanning_files']
        self.assertEqual([row['scanned'] for row in discovering], [1, 2, 3, 4])
        self.assertEqual([row['scanned_bytes'] for row in discovering], [10, 30, 40, 60])
        self.assertTrue(all(row['total'] == 0 for row in discovering))
        self.assertEqual((result.scanned, result.scanned_bytes), (4, 60))
