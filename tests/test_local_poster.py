"""同目录封面的隔离读取、超时回收与独立包入口。"""
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from peach.local_poster import read_jpeg


class LocalPosterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / '示例封面.png'
        Image.new('RGB', (24, 36), 'teal').save(self.source)

    def test_child_reads_a_unicode_path_and_returns_jpeg(self):
        data, size = read_jpeg(self.source)
        self.assertEqual(size, (24, 36))
        self.assertTrue(data.startswith(b'\xff\xd8'))

    def test_unreadable_image_returns_a_retryable_error(self):
        self.source.write_bytes(b'not an image')
        with self.assertRaisesRegex(ValueError, '无法读取'):
            read_jpeg(self.source)

    def test_local_disk_picture_decodes_without_starting_a_process(self):
        expected = read_jpeg(self.source)
        with patch('peach.local_poster.subprocess.run', side_effect=AssertionError('本地图片不启动子进程')):
            self.assertEqual(read_jpeg(self.source, isolated=False), expected)

    def test_timeout_terminates_and_reaps_the_child(self):
        from peach import local_poster
        original_run = subprocess.run
        original_popen = subprocess.Popen
        children = []
        def spawn(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child
        def blocked(command, **options):
            return original_run([sys.executable, '-c', 'import time; time.sleep(60)'], **options)
        with patch.object(local_poster, 'TIMEOUT_SECONDS', 0.2), \
                patch.object(local_poster.subprocess, 'run', side_effect=blocked), \
                patch.object(local_poster.subprocess, 'Popen', side_effect=spawn), \
                self.assertRaises(subprocess.TimeoutExpired):
            read_jpeg(self.source)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_packaged_command_uses_the_cli_worker(self):
        from peach.cli import main
        output = self.root / 'worker.jpg'
        self.assertEqual(main(['local-image', str(self.source), str(output)]), 0)
        with Image.open(output) as image:
            self.assertEqual(image.size, (24, 36))

    def test_picture_batches_use_the_selected_concurrency_and_one_writer(self):
        from peach.library_processing import _read_local_posters
        data, size = read_jpeg(self.source)
        for mode, workers in (('low', 1), ('standard', 2), ('high', 4)):
            with self.subTest(mode=mode):
                barrier = threading.Barrier(workers, timeout=3)
                readers, writers = set(), set()
                jobs = [(dict(id=index, name=f'{index}.mp4'), self.source, str(index),
                         self.root / mode / f'{index}.jpg') for index in range(workers)]
                def read(_source, *, readers=readers, barrier=barrier, **_options):
                    readers.add(threading.get_ident())
                    barrier.wait()
                    return data, size
                state = dict(speed_mode=mode, covers=0)
                with patch('peach.local_poster.read_jpeg', side_effect=read):
                    failed = _read_local_posters(jobs, state,
                        update=lambda writers=writers, **_: writers.add(threading.get_ident()),
                        issue=lambda *_args, **_kwargs: self.fail('图片应可读取'))
                self.assertEqual(len(readers), workers)
                self.assertEqual(writers, {threading.get_ident()})
                self.assertEqual(state['covers'], workers)
                self.assertEqual(failed, set())

    def test_stop_does_not_start_the_next_picture_batch(self):
        from peach.library_processing import _read_local_posters
        data, size = read_jpeg(self.source)
        jobs = [(dict(id=index, name=f'{index}.mp4'), self.source, str(index),
                 self.root / f'{index}.jpg') for index in range(5)]
        def update(**values):
            if values.get('checked') == 1:
                raise InterruptedError('停止')
        with patch('peach.local_poster.read_jpeg', return_value=(data, size)) as read, \
                self.assertRaises(InterruptedError):
            _read_local_posters(jobs, dict(speed_mode='standard', covers=0),
                                update=update, issue=lambda *_args, **_kwargs: None)
        self.assertEqual(read.call_count, 2)

    def test_idle_reader_takes_another_picture_while_one_is_waiting(self):
        from peach.library_processing import _read_local_posters
        data, size = read_jpeg(self.source)
        third_started = threading.Event()
        jobs = [(dict(id=index, name=f'{index}.mp4'), Path(str(index)), str(index),
                 self.root / f'{index}.jpg') for index in range(3)]
        def read(source, **_options):
            if str(source) == '0':
                self.assertTrue(third_started.wait(3))
            if str(source) == '2':
                third_started.set()
            return data, size
        with patch('peach.local_poster.read_jpeg', side_effect=read):
            failed = _read_local_posters(jobs, dict(speed_mode='standard', covers=0),
                update=lambda **_: None, issue=lambda *_args, **_kwargs: self.fail('图片应可读取'))
        self.assertEqual(failed, set())

    def test_same_release_installs_one_shared_cover(self):
        from peach.library_processing import _read_local_posters
        data, size = read_jpeg(self.source)
        target = self.root / 'covers' / 'ABW-101.jpg'
        jobs = [(dict(id=index, name=f'ABW-101-{index}.mp4'), self.source, 'ABW-101', target)
                for index in range(2)]
        state = dict(speed_mode='standard', covers=0)
        with patch('peach.local_poster.read_jpeg', return_value=(data, size)):
            failed = _read_local_posters(jobs, state, update=lambda **_: None,
                issue=lambda *_args, **_kwargs: self.fail('图片应可读取'))
        self.assertEqual((state['covers'], failed), (1, set()))

    def test_mixed_sources_isolate_only_the_cloud_picture(self):
        from peach.library_processing import _read_local_posters
        jobs = [(dict(id=index, name=f'{index}.mp4', location=location), self.source, str(index),
                 self.root / 'covers' / f'{index}.jpg') for index, location in enumerate(('local', 'pikpak'))]
        state = dict(speed_mode='standard', covers=0)
        with patch('peach.local_poster.subprocess.run', wraps=subprocess.run) as run:
            failed = _read_local_posters(jobs, state, update=lambda **_: None,
                issue=lambda *_args, **_kwargs: self.fail('图片应可读取'))
        self.assertEqual((state['covers'], failed), (2, set()))
        self.assertEqual(run.call_count, 1)
