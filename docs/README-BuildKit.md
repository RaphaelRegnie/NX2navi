# Ocarina Recomp build kit

This kit builds *The Legend of Zelda: Ocarina of Time* (Nintendo Switch 2, game version 1.1.1) as a native Windows
program **on your PC, from your own copy of the game**. The kit contains no game code or data. Everything the build
writes (`ocarina.exe`, the RomFS image, the shader cache, your saves) comes from your game: keep it to yourself.

## What you need

- 64-bit Windows 10 or 11, 16 GB of RAM, a Vulkan 1.3 graphics card with mesh shaders and a current driver (tested on
  NVIDIA).
- Your own dump of the game as a `.nup` file (or its `exefs` and `romfs` folders), with the 1.1.1 update: program
  `0400486024DEC000`, main build `e0f3ccfa`. Other versions are refused.
- About 30 GB free on the disk you build to, besides the dump.

## One-time setup (about 15 minutes)

1. **Python 3.12 or later** from [python.org](https://www.python.org/downloads/windows/). Tick "Add python.exe to PATH"
   in the installer. Then, in a Command Prompt:

   ```bat
   py -m pip install numpy lz4 zstandard zbic
   ```

   `zbic` unpacks the game's compressed program files. It is a separate GPL-2.0 package you install yourself; the kit
   only calls it.

2. **MSYS2** from [msys2.org](https://www.msys2.org/), installed to the default `C:\msys64`. Open **MSYS2 UCRT64** from
   the Start menu and paste:

   ```sh
   pacman -S --needed mingw-w64-ucrt-x86_64-clang mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-sdl3 mingw-w64-ucrt-x86_64-opus mingw-w64-ucrt-x86_64-libvpx mingw-w64-ucrt-x86_64-dav1d mingw-w64-ucrt-x86_64-zstd mingw-w64-ucrt-x86_64-vulkan-headers mingw-w64-ucrt-x86_64-vulkan-loader mingw-w64-ucrt-x86_64-vulkan-memory-allocator
   ```

   Answer `Y` when it asks. If MSYS2 is somewhere other than `C:\msys64`, add `--msys <folder>\ucrt64` to the build
   command.

## Build (about 10 to 25 minutes, once)

Unpack this kit anywhere, open a Command Prompt in its folder and run:

```bat
py -I tools\make_game.py --nup "C:\path\to\your game.nup" --out "C:\Games\Ocarina"
```

With extracted folders instead, use `--exefs "C:\path\exefs" --romfs "C:\path\romfs"` in place of `--nup`. To only
check that your dump is the right version, add `--check` (it builds nothing).

The build checks the version, unpacks the program files, packs the 26 GB RomFS image (every file is checked against
the dump's own checksums), turns the game's code into C and compiles it (about 6 minutes on a 16-thread Ryzen 7; longer on fewer cores),
then prepares every one of the game's shaders for your graphics card (about 5 more minutes), so the game does not stop
to compile shaders while you play.
You can use your PC meanwhile. If it stops,
run the same command again: finished steps are skipped.

When it says `finished`, start `C:\Games\Ocarina\ocarina.exe`. You can delete `C:\Games\Ocarina\build-cache` (about
5 GB) afterwards.

## Playing

- **F1** opens the settings menu. Settings are saved in `nx2navi.ini` beside the game; saves go to `profile\`.
- The first time a shader appears it is translated, which can stutter briefly; it is cached from then on. After an
  update of the kit or your graphics driver, the next start shows "Preparing shaders" once.
- Logs are in `logs\`. When reporting a problem, say where you were in the game and which graphics card you have, and
  attach `logs\nx2navi.log`. Never send game files.

## If something goes wrong

| Message | What to do |
|---|---|
| `not game version 1.1.1` | Install the 1.1.1 update and dump the game again |
| `the Python package ... is missing` | Run the `pip install` line above |
| `...\clang.exe is missing` | Run the `pacman` line above in MSYS2 UCRT64 |
| `needs ... GB free` | Free space on the `--out` disk, or build to another one |
| The game opens and closes | Update your graphics driver, then send `logs\nx2navi.log` |
