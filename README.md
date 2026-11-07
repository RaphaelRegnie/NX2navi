# NX2navi

**NX2navi** is a static recompilation of *The Legend of Zelda: Ocarina of Time* for Nintendo Switch 2 (game
version 1.1.1) into a native x86-64 application for Windows, with a Vulkan renderer
([docs/architecture.md](docs/architecture.md)).

> [!IMPORTANT]
> **No game files are included.** You need your own legally obtained copy of the game, dumped from your own console.
> Instructions for obtaining those files will not be provided.
>
> The game is built from your files on your machine, so the result contains the game. It is for your own use. Do not share it.

## Status

Early development. Kokiri Forest and the areas around it play at 60 fps and above; visual bugs and crashes are still
expected.

Report problems as issues, following [CONTRIBUTING.md](CONTRIBUTING.md).

## Features

- Native code, not emulation
- 60 fps by default, or a limit of 30 to 240 fps, the display's refresh rate or unlimited
- Native widescreen and ultrawide (the window's shape, 21:9 or 32:9) with the HUD, menus and movies kept 16:9
- Resolutions above the game's 1440p: 1800p, 4K, 5K or the window's own size, sharp on high-DPI displays
- Upscaling and anti-aliasing: NVIDIA DLSS (DLAA, Quality, Balanced, Performance, Ultra Performance; RTX GPUs) or
  AMD FSR 3.1 (Native AA and the same modes; any GPU), which render the 3D scene at the mode's share and keep the HUD
  sharp; or AMD FSR 1 or bilinear scaling to the window, with a sharpness setting; black bars or stretch
- Frame generation with AMD FSR 3.1 (any GPU): a generated frame between every two the game draws, with the HUD kept
  out of it, for about twice the frame rate on a fast display
- HDR on HDR displays: the game's own native HDR, or an enhanced HDR made from its SDR picture
- Every shader prepared when the game is built, so play does not stop to compile them
- In-game settings menu (F1, or Minus and Plus together on a gamepad) and performance overlay (F10)
- Keyboard, mouse and gamepads with remapping, Nintendo or Xbox button layout, rumble and gyro; the game follows
  whichever connected gamepad you press
- Stereo or 5.1 surround sound output
- Docked or handheld console mode
- Graphics cards with less than 20 GiB of video memory keep the game's textures compressed (BC3), about half the
  video memory at the same speed

## Installing

Download `NX2navi-Setup-<version>.exe` from the releases and start it next to your `.nup` dump. The launcher it
installs finds the dump, downloads the build tools, builds the game on your PC (10 to 25 minutes, once) and starts
it; afterwards it keeps the port up to date and rebuilds only what changed ([docs/launcher.md](docs/launcher.md)).
[docs/README-BuildKit.md](docs/README-BuildKit.md) describes the same build by hand, [docs/building.md](docs/building.md)
building from source. Settings are in the in-game menu and in `nx2navi.ini` ([nx2navi.ini.example](nx2navi.ini.example)
lists every key). Controls: [docs/controls.md](docs/controls.md).

Requirements: Windows 10 or later, an x86-64 CPU with SSE4.2 (AVX2/FMA machines get a faster build), a GPU with Vulkan
1.3, `VK_EXT_external_memory_host` and `VK_EXT_mesh_shader`.

## License

This repository is private. Its additions and changes are all rights reserved ([LICENSE-ADDITIONS](LICENSE-ADDITIONS)):
no part of them may be copied, published, or used to train, fine-tune or prompt AI models or AI coding tools without
written permission. The base import stays under its [MIT License](LICENSE), and third-party code keeps its own license
([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). No license grants anything for Nintendo's game.

NX2navi is an independent, non-commercial fan project, not affiliated with or endorsed by Nintendo. *The Legend
of Zelda*, *Ocarina of Time* and *Nintendo Switch* are trademarks of Nintendo.

## Building from source

[docs/building.md](docs/building.md) lists the toolchain and the steps. Everything the build writes stays under
`build\` and `generated\`, which git ignores; run `tools/check_content.py` before a commit.

Most of the code and documentation is written by AI coding agents (Claude Code) under the maintainer's direction; the
maintainer plays every build and decides what is merged. [AGENTS.md](AGENTS.md) holds the rules they work under.
