#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
output="${1:?Usage: bash scripts/build_untrunc_windows.sh OUTPUT}"
mkdir -p "$output"
output="$(cd "$output" && pwd)"
python3 "$script_dir/fetch_untrunc_sources.py" prepare "$output"

{
  x86_64-w64-mingw32-gcc --version
  x86_64-w64-mingw32-g++ --version
  make --version
  nasm -v
  python3 --version
} > "$output/build-environment.txt"

cd "$output/work/ffmpeg"
./configure \
  --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32- \
  --pkg-config=pkg-config --disable-autodetect --disable-everything \
  --disable-programs --disable-doc --disable-debug --disable-shared --enable-static \
  --disable-network --disable-avdevice --disable-avfilter --disable-swscale --disable-swresample \
  --enable-protocol=file --enable-demuxer=mov \
  --enable-decoder=h264,hevc,mpeg4,aac,mp3,alac,amrnb,amrwb,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,mjpeg,prores \
  --enable-parser=h264,hevc,aac,mpegaudio \
  --extra-cflags=-O2
make -j"$(nproc)"
cp ffbuild/config.mak "$output/buildconf.txt"

cd "$output/work/untrunc"
revision="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$script_dir/untrunc-sources.json")"
untrunc_cxxflags="-O3 -std=c++17 -D_FILE_OFFSET_BITS=64 -I$output/work/ffmpeg"
untrunc_ldflags="-static -static-libgcc -static-libstdc++ -L$output/work/ffmpeg/libavformat -lavformat -L$output/work/ffmpeg/libavcodec -lavcodec -L$output/work/ffmpeg/libavutil -lavutil -lbcrypt -lws2_32 -lsecur32 -lm -s"
make -j"$(nproc)" FF_VER=shared IS_RELEASE=1 EXE=untrunc.exe \
  CXX=x86_64-w64-mingw32-g++ \
  CXXFLAGS="$untrunc_cxxflags" \
  VER="$revision" \
  LDFLAGS="$untrunc_ldflags"
printf '\nUNTRUNC_REVISION=%s\nUNTRUNC_CXXFLAGS=%s\nUNTRUNC_LDFLAGS=%s\n' \
  "$revision" "$untrunc_cxxflags" "$untrunc_ldflags" >> "$output/buildconf.txt"
mkdir -p "$output/untrunc"
cp untrunc.exe "$output/untrunc/untrunc.exe"
cp /usr/share/doc/g++-mingw-w64-x86-64-posix/copyright "$output/untrunc/LICENSE-GCC-runtime.txt"
cp /usr/share/doc/mingw-w64-x86-64-dev/copyright "$output/untrunc/LICENSE-MinGW-w64.txt"
python3 "$script_dir/fetch_untrunc_sources.py" finish "$output"
