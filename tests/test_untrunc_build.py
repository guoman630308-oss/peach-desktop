"""untrunc 固定源码与发行清单的完整性。"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'scripts'), *sys.path]):
    spec = importlib.util.spec_from_file_location('fetch_untrunc_sources', ROOT / 'scripts/fetch_untrunc_sources.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)


class UntruncSourceBuildTests(unittest.TestCase):
    def test_sources_pin_the_compatible_ffmpeg_branch_and_untrunc_revision(self):
        lock = build.load_sources()
        sources = {source['name']: source for source in lock['sources']}
        self.assertEqual(lock['license'], 'GPL-2.0-or-later')
        self.assertEqual(sources['ffmpeg']['revision'], 'n8.1.3')
        self.assertEqual(len(sources['untrunc']['revision']), 40)
        self.assertEqual(lock['version'], sources['untrunc']['revision'][:7])

    def test_source_lock_rejects_missing_dependencies_and_unsafe_archives(self):
        lock = build.load_sources()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'sources.json'
            lock['sources'][0]['archive'] = '../untrunc.tar.gz'
            path.write_text(json.dumps(lock), encoding='utf-8')
            with self.assertRaises(ValueError):
                build.load_sources(path)
            lock['sources'] = lock['sources'][1:]
            path.write_text(json.dumps(lock), encoding='utf-8')
            with self.assertRaises(ValueError):
                build.load_sources(path)

    def fixture(self, output):
        for name, payload in {
            'untrunc/untrunc.exe': b'MZ-untrunc-fixture',
            'work/untrunc/COPYING': b'untrunc-license',
            'work/ffmpeg/COPYING.LGPLv2.1': b'ffmpeg-license',
            'buildconf.txt': b'--disable-autodetect --disable-network',
            'build-environment.txt': b'cross compiler version',
            'sources/untrunc.tar.gz': b'untrunc-source-fixture',
            'sources/ffmpeg.tar.xz': b'ffmpeg-source-fixture',
        }.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        lock = {
            'version': 'abcdef0', 'license': 'GPL-2.0-or-later',
            'sources': [{'name': name, 'revision': revision, 'archive': archive,
                         'url': f'https://example.test/{archive}',
                         'sha256': build.digest(output / 'sources' / archive)}
                        for name, revision, archive in [('untrunc', 'abcdef0', 'untrunc.tar.gz'),
                                                         ('ffmpeg', 'n8.1.3', 'ffmpeg.tar.xz')]],
        }
        return lock

    def test_manifest_hashes_all_distributed_files_and_corresponding_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve()
            lock = self.fixture(output)
            manifest = build.finish(output, lock)
            self.assertEqual(manifest['version'], lock['version'])
            bundle = output / 'untrunc'
            self.assertEqual(set(manifest['files']), {path.name for path in bundle.iterdir()} - {'manifest.json'})
            for name, digest in manifest['files'].items():
                self.assertEqual(build.digest(bundle / name), digest)
            source_archive = output / manifest['source_archive']['name']
            self.assertEqual(build.digest(source_archive), manifest['source_archive']['sha256'])
            with zipfile.ZipFile(source_archive) as archive:
                for source in lock['sources']:
                    self.assertEqual(archive.read('sources/' + source['archive']),
                                     (output / 'sources' / source['archive']).read_bytes())
                self.assertTrue({'scripts/build_untrunc_windows.sh', 'scripts/fetch_ffmpeg_sources.py',
                                 'scripts/fetch_untrunc_sources.py', 'scripts/untrunc-sources.json',
                                 'README.md', 'buildconf.txt', 'build-environment.txt'} <= set(archive.namelist()))

    def test_corrupt_source_archive_prevents_a_release_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve()
            lock = self.fixture(output)
            (output / 'sources/untrunc.tar.gz').write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                build.finish(output, lock)
            self.assertFalse((output / 'untrunc/manifest.json').exists())


@unittest.skipUnless(all(os.environ.get(name) for name in (
    'PEACH_TEST_UNTRUNC', 'PEACH_TEST_FFMPEG', 'PEACH_TEST_FFPROBE',
)), '构建验收需要显式提供三份原生工具')
class UntruncNativeRecoveryTests(unittest.TestCase):
    def test_native_distribution_has_matching_hashes_and_internal_ffmpeg_version(self):
        executable = Path(os.environ['PEACH_TEST_UNTRUNC']).resolve()
        bundle = executable.parent
        manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['license'], 'GPL-2.0-or-later')
        self.assertEqual(set(manifest['files']), {path.name for path in bundle.iterdir()} - {'manifest.json'})
        for name, digest in manifest['files'].items():
            self.assertEqual(build.digest(bundle / name), digest)
        archive = bundle.parent / manifest['source_archive']['name']
        self.assertEqual(build.digest(archive), manifest['source_archive']['sha256'])
        version = subprocess.run([str(executable), '-V'], check=True, capture_output=True,
                                 text=True, encoding='utf-8', timeout=15).stdout
        self.assertIn(manifest['version'], version)
        self.assertIn("ffmpeg '8.1.3'", version)

    def test_a_synthetic_truncated_mp4_is_recovered_and_decodes(self):
        from peach import mp4recover
        from peach.mp4repair import _top_level

        ffmpeg = Path(os.environ['PEACH_TEST_FFMPEG']).resolve()
        ffprobe = Path(os.environ['PEACH_TEST_FFPROBE']).resolve()
        untrunc = Path(os.environ['PEACH_TEST_UNTRUNC']).resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            reference = root / 'reference.mp4'
            subprocess.run([
                str(ffmpeg), '-hide_banner', '-v', 'error', '-f', 'lavfi',
                '-i', 'testsrc2=size=128x96:rate=10', '-f', 'lavfi',
                '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(reference),
            ], check=True, capture_output=True, timeout=60)
            with reference.open('rb') as stream:
                boxes = list(_top_level(stream))
            self.assertEqual(boxes[-1][0], b'moov')
            broken = root / 'broken.mp4'
            broken.write_bytes(reference.read_bytes()[:boxes[-1][1]])
            self.assertTrue(mp4recover.missing_index(broken))
            size = broken.stat().st_size
            result = mp4recover.recover(broken, [reference], untrunc=untrunc,
                                        ffmpeg=ffmpeg, ffprobe=ffprobe, work=root / 'recovered')
            self.assertTrue(result.path.is_file())
            self.assertFalse(mp4recover.missing_index(result.path))
            self.assertAlmostEqual(result.duration, 2, delta=.2)
            self.assertGreater(result.coverage, .95)
            self.assertEqual(broken.stat().st_size, size)


if __name__ == '__main__':
    unittest.main()
