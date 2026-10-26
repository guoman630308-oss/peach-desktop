#!/usr/bin/env bash
# 在 Linux 使用 MinGW-w64 构建 Windows x64 FFmpeg 与完整对应源码包。
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output="$(realpath -m -- "${1:?用法: build_ffmpeg_windows.sh <输出目录>}")"
for tool in python3 make nasm pkg-config clang meson ninja x86_64-w64-mingw32-gcc x86_64-w64-mingw32-strip; do
    command -v "$tool" >/dev/null || { echo "缺少构建工具: $tool" >&2; exit 1; }
done
python3 "$script_dir/fetch_ffmpeg_sources.py" prepare "$output"
work="$output/work"
prefix="$work/prefix"
{
    x86_64-w64-mingw32-gcc --version
    nasm -v
    clang --version
    make --version
    meson --version
    ninja --version
    python3 --version
    uname -a
} > "$output/build-environment.txt"
export PKG_CONFIG_PATH=
export PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
mkdir -p "$prefix" "$output/ffmpeg/bin"
meson setup "$work/dav1d-build" "$work/dav1d" --cross-file "$script_dir/dav1d-mingw64.ini" \
    --prefix "$prefix" --libdir lib --buildtype release --default-library static \
    -Denable_tools=false -Denable_tests=false
meson compile -C "$work/dav1d-build"
meson install -C "$work/dav1d-build"
cd "$work/x264"
./configure --host=x86_64-w64-mingw32 --cross-prefix=x86_64-w64-mingw32- \
    --prefix="$prefix" --enable-static --disable-cli --disable-opencl \
    --disable-avs --disable-swscale --disable-lavf --disable-ffms --disable-gpac --disable-lsmash \
    --extra-ldflags=-static
make -j"$(nproc)"
make install
make -C "$work/nv-codec-headers" PREFIX="$prefix" install
cd "$work/ffmpeg"
./configure --prefix="$output/ffmpeg" --target-os=mingw32 --arch=x86_64 \
    --cross-prefix=x86_64-w64-mingw32- --enable-cross-compile \
    --disable-autodetect --disable-shared --enable-static --disable-debug \
    --disable-doc --disable-ffplay --disable-pthreads --enable-w32threads \
    --enable-gpl --enable-version3 --disable-nonfree --enable-libx264 --enable-libdav1d \
    --enable-cuda-llvm \
    --enable-ffnvcodec --enable-cuda --enable-cuvid --enable-nvenc --enable-nvdec \
    --enable-d3d11va --enable-dxva2 --enable-schannel \
    --extra-cflags="-I$prefix/include" --extra-ldflags="-L$prefix/lib -static" \
    --pkg-config=pkg-config --pkg-config-flags=--static
make -j"$(nproc)"
cp ffmpeg.exe ffprobe.exe "$output/ffmpeg/bin/"
x86_64-w64-mingw32-strip "$output/ffmpeg/bin/ffmpeg.exe" "$output/ffmpeg/bin/ffprobe.exe"
cp /usr/share/doc/mingw-w64-common/copyright "$output/ffmpeg/LICENSE-MinGW-w64.txt"
cp /usr/share/doc/gcc-mingw-w64-base/copyright "$output/ffmpeg/LICENSE-GCC-runtime.txt"
cp ffbuild/config.mak "$output/buildconf.mak"
python3 "$script_dir/fetch_ffmpeg_sources.py" finish "$output"
