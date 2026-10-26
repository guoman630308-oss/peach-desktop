"""将运行时资源装入 wheel，源码树仍是资源的唯一维护位置。"""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        destination = Path(self.build_lib) / "peach" / "_resources"
        for name in ("migrations", "web", "resources"):
            self.copy_tree(str(root / name), str(destination / name))


setup(cmdclass={"build_py": BuildWithResources})
