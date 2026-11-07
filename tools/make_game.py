"""Build Ocarina of Time (Switch 2, game 1.1.1) for Windows on the player's PC, from the player's own game files.

    python -I tools/make_game.py --nup "C:\\path\\game.nup" --out "C:\\Games\\Ocarina"
    python -I tools/make_game.py --exefs "C:\\path\\exefs" --romfs "C:\\path\\romfs" --out "C:\\Games\\Ocarina"
    python -I tools/make_game.py --nup "C:\\path\\game.nup" --check          # only checks the game's version

Steps, each skipped when a run before finished it for the same input (state in <out>/build-cache/state.json):
  1. check    the title (0400486024DEC000) and main's build id (game 1.1.1)
  2. exefs    the five ExeFS files, every module's segments unpacked (ZBIC through the player's own `zbic` package)
              and stored plainly, as the runtime's loader reads them -> <out>/exefs
  3. romfs    every RomFS file, checked against the container's sha256, into one image -> <out>/romfs/romfs.bin and
              index.json (16-byte aligned, in the byte order of the paths)
  4. lift     recomp/gen.py with recomp/patches-v111.txt -> <out>/build-cache/lift, plus the two SDK bring-up fixes
  5. compile  the lifted C and runtime/ with MSYS2's clang, linked into <out>/ocarina.exe; the MSYS2 DLLs it loads,
              the shader translator and romfs-index.bin beside it
  6. shaders  every shader in the game files (tools/prepare_shaders.py, default bind state) translated, and each one's
              GPU driver build done once, so play does not stop to compile them (`ocarina.exe --prepare-shaders`)

Nothing is written outside --out. The game folder then holds only what this PC built from the player's files: never
share it. Needs Python 3.10+ with numpy, lz4, zstandard and zbic (pip), and MSYS2 UCRT64's clang and libraries
(README-BuildKit.md lists the commands). Delete <out>/build-cache afterwards to free about 5 GB.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'tools'))
import nup  # noqa: E402

TITLE_ID = 0x0400486024DEC000
MAIN_BUILD_ID = 'e0f3ccfaf50065ae4ea01eb5aeb13b1a'   # main of game 1.1.1
EXEFS_FILES = ('main', 'main.npdm', 'sdk', 'subsdk0', 'rtld')
MODULES = ('main', 'sdk', 'subsdk0', 'rtld')
ALIGN = 16
STATE_VERSION = 1

# Bring-up fixes in the lifted SDK, each anchored on exact generated text that must occur once.
LIFT_FIXES = [
    # nn::oe::SetupGpuErrorHandler (sdk+0x59a430) registers a system worker the host does not provide: GPU error
    # reporting is not needed, so it returns at once.
    ('sdk', 'void f_700579a430(Cpu *c) {\n    FN_ENTER(0x700579a430ull);\n',
     'void f_700579a430(Cpu *c) {\n    c->x[0] = 0; return;   /* bring-up: GpuErrorHandler (make_game.py) */\n'
     '    FN_ENTER(0x700579a430ull);\n'),
    # The thread-start path loads TPIDR_EL0 from TLR+0x1F0 before the SDK stored its TLS there for a host-created
    # thread: a zero slot keeps the TLS pointer the host seeded.
    ('sdk', '    { uint64_t ea = c->x[19] + 0x1b8ull; ST64(ea, x8); }\n    x8 = (uint64_t)(c->tpidrro);\n'
     '    { uint64_t ea = x8 + 0x1f0ull; uint64_t v = LD64(ea); x8 = (uint64_t)(v); }\n    c->tpidr = x8;\n',
     '    { uint64_t ea = c->x[19] + 0x1b8ull; ST64(ea, x8); }\n    x8 = (uint64_t)(c->tpidrro);\n'
     '    { uint64_t ea = x8 + 0x1f0ull; uint64_t v = LD64(ea); x8 = (uint64_t)(v); }\n'
     '    if (x8) c->tpidr = x8;   /* zero slot: keep the host\'s TLS (make_game.py) */\n'),
]
LINK_LIBS = ['-lvulkan-1', '-lSDL3', '-lopus', '-lvpx', '-l:libdav1d.a', '-lzstd', '-l:libatomic.a', '-lbcrypt',
             '-limm32', '-luuid', '-lole32', '-luser32', '-lshell32', '-lgdi32', '-ladvapi32', '-lwinmm']
SYSTEM_DLLS_SKIP = {'vulkan-1.dll'}   # the graphics driver's loader, never a copy


def say(msg):
    print(msg, flush=True)


def fail(msg):
    print(f'\nerror: {msg}', file=sys.stderr, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------- input

class Source:
    """The game files, from a .nup container or from extracted exefs/romfs folders."""

    def __init__(self, a):
        self.n = None
        if a.nup:
            try:
                self.n = nup.Nup(a.nup)
            except (OSError, nup.NupError) as e:
                fail(f'cannot read {a.nup}: {e}')
            # The base title and its update (title id + 0x800), the update first and the newest of each kind first.
            # A module comes from the first of them that holds it whole: an update's ExeFS may leave a module out or
            # hold something else under its name.
            pkgs = [p for p in self.n.packages if p['title_id'] in (TITLE_ID, TITLE_ID | 0x800)]
            if not pkgs:
                ids = ', '.join(f"{p['title_id']:016X}" for p in self.n.packages)
                fail(f'this .nup holds {ids}, not Ocarina of Time (0400486024DEC000)')
            pkgs.sort(key=lambda p: (p['kind'] != 1, -p['version']))
            self.exefs = {}
            for name in EXEFS_FILES:
                for p in pkgs:
                    fi = dict(self.n.tree(p['exefs'])).get(name) if p['exefs'] < self.n.ndir else None
                    magic = b'META' if name.endswith('.npdm') else b'NSO0'
                    if fi is not None and self.n.size(fi) >= 0x100 and next(self.n.chunks(fi))[:4] == magic:
                        self.exefs[name] = fi
                        break
            self.romfs = []
            for p in pkgs:
                if p['romfs'] < self.n.ndir:
                    self.romfs = list(self.n.tree(p['romfs']))
                if self.romfs:
                    break
            st = os.stat(a.nup)
            self.identity = f'nup {os.path.abspath(a.nup)} {st.st_size} {int(st.st_mtime)}'
        else:
            self.exefs_dir, self.romfs_dir = Path(a.exefs), Path(a.romfs)
            for d in (self.exefs_dir, self.romfs_dir):
                if not d.is_dir():
                    fail(f'{d} is not a folder')
            self.exefs = {name: self.exefs_dir / name for name in EXEFS_FILES if (self.exefs_dir / name).is_file()}
            self.romfs = [(p.relative_to(self.romfs_dir).as_posix(), p) for p in self.romfs_dir.rglob('*') if p.is_file()]
            self.identity = f'folders {self.exefs_dir.resolve()} {self.romfs_dir.resolve()} {len(self.romfs)}'
        missing = [x for x in EXEFS_FILES if x not in self.exefs]
        if missing:
            fail(f'the ExeFS lacks {", ".join(missing)} (or holds something else under that name)')
        if not self.romfs:
            fail('the RomFS is empty')

    def read_exefs(self, name):
        return self.n.read(self.exefs[name]) if self.n else self.exefs[name].read_bytes()

    def romfs_size(self, ref):
        return self.n.size(ref) if self.n else ref.stat().st_size

    def romfs_chunks(self, ref):
        if self.n:
            yield from self.n.chunks(ref)
            return
        with open(ref, 'rb') as f:
            while b := f.read(nup.CHUNK):
                yield b


def check(src):
    main = src.read_exefs('main')
    if main[:4] != b'NSO0':
        fail('main is not an NSO module')
    build = main[0x40:0x50].hex()
    if build != MAIN_BUILD_ID:
        fail(f'this is not game version 1.1.1: main has build {build[:8]}, the recomp supports build {MAIN_BUILD_ID[:8]}. '
             'Install the 1.1.1 update and dump the game again.')
    return main


# ---------------------------------------------------------------- exefs

def flat_nso(raw, name):
    """An NSO with its segments stored uncompressed (the runtime's loader reads only those); segment hashes kept."""
    flags = struct.unpack_from('<I', raw, 0xC)[0]
    out = bytearray(raw[:0x100])
    for i, seg in enumerate(('text', 'ro', 'data')):
        file_off, mem_off, size = struct.unpack_from('<III', raw, 0x10 + i * 0x10)
        stored = struct.unpack_from('<I', raw, 0x60 + i * 4)[0]
        data = raw[file_off:file_off + (stored if flags >> i & 1 else size)]
        if flags >> i & 1:
            if flags & 0x80:
                try:
                    import zbic
                except ImportError:
                    fail('the game\'s modules need the zbic package: pip install zbic')
                data = zbic.decompress(data)
            else:
                import lz4.block
                data = lz4.block.decompress(data, uncompressed_size=size)
        if len(data) != size:
            fail(f'{name}: {seg} unpacks to {len(data):#x} bytes, its header says {size:#x}')
        if flags >> (3 + i) & 1 and hashlib.sha256(data).digest() != raw[0xA0 + 0x20 * i:0xC0 + 0x20 * i]:
            fail(f'{name}: the {seg} segment is damaged (sha256 mismatch)')
        struct.pack_into('<III', out, 0x10 + i * 0x10, len(out), mem_off, size)
        struct.pack_into('<I', out, 0x60 + i * 4, size)
        out += data
    struct.pack_into('<I', out, 0xC, flags & ~0x87)   # segments stored plainly, no ZBIC
    return bytes(out)


def step_exefs(src, out):
    dst = out / 'exefs'
    dst.mkdir(parents=True, exist_ok=True)
    for name in EXEFS_FILES:
        raw = src.read_exefs(name)
        data = flat_nso(raw, name) if name in MODULES else raw
        (dst / name).write_bytes(data)
        say(f'  {name:9} {len(raw) / 1e6:6.1f} MB -> {len(data) / 1e6:6.1f} MB')


# ---------------------------------------------------------------- romfs

def step_romfs(src, out, jobs):
    dst = out / 'romfs'
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.romfs, key=lambda e: e[0].encode())
    total = sum(src.romfs_size(ref) for _, ref in files)
    free = shutil.disk_usage(dst).free
    if free < total + (1 << 30):
        fail(f'{dst} needs {total / 1e9:.1f} GB free for the RomFS image, it has {free / 1e9:.1f} GB')
    say(f'  {len(files)} files, {total / 1e9:.1f} GB')
    entries, h, pos, done, t0 = [], hashlib.sha256(), 0, 0, time.time()
    part = dst / 'romfs.bin.part'

    def load(e):   # small files are read whole on a worker; big ones are streamed by the writer
        rel, ref = e
        return None if src.romfs_size(ref) > (64 << 20) else b''.join(src.romfs_chunks(ref))

    with open(part, 'wb', buffering=16 << 20) as img, concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        batch = 256
        for start in range(0, len(files), batch):
            group = files[start:start + batch]
            for (rel, ref), data in zip(group, ex.map(load, group)):
                pad = -pos % ALIGN
                if pad:
                    img.write(b'\0' * pad)
                    h.update(b'\0' * pad)
                    pos += pad
                size = src.romfs_size(ref)
                entries.append({'path': rel, 'offset': pos, 'size': size})
                for chunk in ((data,) if data is not None else src.romfs_chunks(ref)):
                    img.write(chunk)
                    h.update(chunk)
                pos += size
                done += size
            if start // batch % 40 == 0:
                say(f'  {done / 1e9:5.1f} of {total / 1e9:.1f} GB, {time.time() - t0:.0f} s')
    part.replace(dst / 'romfs.bin')
    index = {'status': 'pass', 'romfs_size': pos, 'romfs_sha256': h.hexdigest(), 'files': entries}
    (dst / 'index.json').write_text(json.dumps(index), encoding='utf-8')
    say(f'  image {pos / 1e9:.2f} GB in {time.time() - t0:.0f} s')


# ---------------------------------------------------------------- lift

def step_lift(out, cache):
    lift = cache / 'lift'
    if lift.exists():
        shutil.rmtree(lift)
    cmd = [sys.executable, str(KIT / 'recomp' / 'gen.py'), '--exefs', str(out / 'exefs'), '--out', str(lift),
           '--patches', str(KIT / 'recomp' / 'patches-v111.txt')]
    if subprocess.run(cmd).returncode:
        fail('the code generator failed (see above)')
    for mod, old, new in LIFT_FIXES:
        hits = [p for p in sorted((lift / mod).glob('*.c')) if old in p.read_text(encoding='utf-8')]
        if len(hits) != 1 or hits[0].read_text(encoding='utf-8').count(old) != 1:
            fail(f'a bring-up fix found its anchor {len(hits)} times in the lifted {mod}, not once')
        p = hits[0]
        p.write_text(p.read_text(encoding='utf-8').replace(old, new), encoding='utf-8', newline='\n')


# ---------------------------------------------------------------- compile

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while b := f.read(8 << 20):
            h.update(b)
    return h.hexdigest()


def modules_c(lift, out, gen):
    layout = json.loads((lift / 'layout.json').read_text(encoding='utf-8'))
    L = ['#include "dispatch.h"', '#include "host.h"', '#include "layout.h"', '']
    for m in layout:
        n = m['name']
        L += [f'extern const uint64_t {n}_fn_addrs[];', f'extern const GuestFn {n}_fn_ptrs[];',
              f'extern const uint64_t {n}_fn_count;']
    L += ['', 'const RtLayoutEntry rt_layout[] = {']
    L += [f'    {{"{m["name"]}", MOD_{m["name"].upper()}_BASE, MOD_{m["name"].upper()}_SIZE}},' for m in layout]
    L += ['};', f'const int rt_layout_count = {len(layout)};', '', 'int rt_linked_modules(RtModule *out, int max) {',
          '    int n = 0;']
    for m in layout:
        n, u = m['name'], m['name'].upper()
        L.append(f'    if (n < max) out[n++] = (RtModule){{"{n}", MOD_{u}_BASE, MOD_{u}_SIZE, {n}_fn_addrs, {n}_fn_ptrs, '
                 f'{n}_fn_count}};')
    L += ['    return n;', '}', '', 'const RtFileHash rt_exefs_sha256[] = {']
    L += [f'    {{"{name}", "{sha256_file(out / "exefs" / name)}"}},' for name in EXEFS_FILES]
    L += ['};', f'const int rt_exefs_sha256_count = {len(EXEFS_FILES)};', '',
          f'const char rt_default_exefs[] = {json.dumps(str(out / "exefs"))};',
          f'const char rt_default_romfs[] = {json.dumps(str(out / "romfs"))};',
          f'const char rt_default_profile[] = {json.dumps(str(out / "profile"))};',
          'const int rt_game_version = 111;',
          'const unsigned long long rt_app_pool_bytes = 9809ull << 20;   /* the Switch 2 application pool */',
          'const int rt_nvn_api_version[2] = {231, 40};   /* NVN2: main asks for major 231, minor 40 or later */',
          f'const char rt_port_version[] = {json.dumps(kit_version())};']
    p = gen / 'modules.c'
    p.write_text('\n'.join(L) + '\n', encoding='utf-8')
    (gen / 'shim.c').write_text('/* Defined by the SDL window path only; this build uses the Win32 one. */\n'
                                'void menu_sdl_event(void *e) { (void)e; }\n', encoding='utf-8')
    return p


def kit_version():
    v = KIT / 'VERSION'
    return v.read_text(encoding='utf-8').strip() if v.is_file() else '0.0.1-ocarina'


def pe_imports(path):
    """The DLL names a PE file imports (its import directory), lower case."""
    d = Path(path).read_bytes()
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    nsec, opt = struct.unpack_from('<H', d, pe + 6)[0], struct.unpack_from('<H', d, pe + 20)[0]
    sec = pe + 24 + opt
    rva = struct.unpack_from('<I', d, pe + 24 + 120)[0]   # PE32+ data directory 1: imports

    def off(r):
        for i in range(nsec):
            va, vs, raw = (struct.unpack_from('<I', d, sec + 40 * i + k)[0] for k in (12, 8, 20))
            if va <= r < va + max(vs, 1):
                return r - va + raw
        return None

    names, at = [], off(rva) if rva else None
    while at is not None:
        name_rva = struct.unpack_from('<I', d, at + 12)[0]
        if not name_rva:
            break
        o = off(name_rva)
        names.append(d[o:d.index(b'\0', o)].decode('ascii').lower())
        at += 20
    return names


def copy_dlls(exe, msys_bin, out):
    seen, todo, copied = set(), [exe], []
    while todo:
        for name in pe_imports(todo.pop()):
            src = msys_bin / name
            if name in seen or name in SYSTEM_DLLS_SKIP or not src.is_file():
                continue
            seen.add(name)
            shutil.copy2(src, out / src.name)
            copied.append(src.name)
            todo.append(src)
    return copied


def step_compile(out, cache, msys, jobs):
    lift, gen, obj = cache / 'lift', cache / 'gen', cache / 'obj'
    gen.mkdir(parents=True, exist_ok=True)
    obj.mkdir(parents=True, exist_ok=True)
    rt = KIT / 'runtime'
    clang, clangxx = msys / 'bin' / 'clang.exe', msys / 'bin' / 'clang++.exe'
    env = dict(os.environ, PATH=str(msys / 'bin') + os.pathsep + os.environ.get('PATH', ''))
    if subprocess.run([sys.executable, str(KIT / 'tools' / 'romfs_index.py'), '--romfs', str(out / 'romfs'),
                       '--out', str(gen)]).returncode:
        fail('the RomFS index could not be written')
    srcs = [(p, obj / f'{mod}_{p.stem}.o') for mod in ('main', 'sdk', 'subsdk0') for p in sorted((lift / mod).glob('*.c'))]
    for pat in ('*.c', '*.cpp', 'platform/*.c', 'ui/*.cpp', 'third_party/imgui/*.cpp', 'third_party/imgui/backends/*.cpp'):
        for p in sorted(rt.glob(pat)):
            if p.stem.endswith('_sdl') or p.name == 'linux.c':
                continue
            srcs.append((p, obj / ('rt_' + p.relative_to(rt).as_posix().replace('/', '_').rsplit('.', 1)[0] + '.o')))
    srcs += [(modules_c(lift, out, gen), obj / 'modules.o'), (gen / 'shim.c', obj / 'shim.o')]
    inc = ['-I', str(rt), '-I', str(lift), '-I', str(gen), '-I', str(rt / 'third_party' / 'imgui'),
           '-I', str(msys / 'include' / 'vma')]

    def cc(job):
        src, dst = job
        cpp = src.suffix in ('.cpp', '.cxx')
        # the lifted code at -O1 (its size makes -O2 slow to build), the runtime at -O2; neither may assume strict aliasing:
        # the lifted code reaches guest memory through casts of one address to several types (tools/build_game.py's flags)
        guest = dst.name.startswith(('main_', 'sdk_', 'subsdk0_'))
        cmd = [str(clangxx if cpp else clang), '-c', '-std=gnu++17' if cpp else '-std=gnu11', '-O1' if guest else '-O2',
               '-fno-strict-aliasing', '-w', *inc, '-o', str(dst), str(src)]
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return src, p.returncode, p.stderr[-1500:]

    say(f'  {len(srcs)} files on {jobs} threads')
    t0, done, bad = time.time(), 0, []
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for src, rc, err in ex.map(cc, srcs):
            done += 1
            if rc:
                bad.append((src, err))
            if done % 50 == 0 or done == len(srcs):
                say(f'  compiled {done} of {len(srcs)}, {time.time() - t0:.0f} s')
    if bad:
        for src, err in bad[:3]:
            print(f'--- {src}\n{err}', file=sys.stderr)
        fail(f'{len(bad)} files did not compile')
    # the manifest (per-monitor DPI awareness: the picture at the monitor's real size, not one Windows stretches) and the
    # icon, runtime/nx2navi.rc through MSYS2's windres
    rco = obj / 'nx2navi_rc.o'
    r = subprocess.run([str(msys / 'bin' / 'windres.exe'), '-I', str(rt), '-O', 'coff', '-i', str(rt / 'nx2navi.rc'),
                        '-o', str(rco)], capture_output=True, text=True, env=env)
    if r.returncode:
        fail(f'windres failed on runtime/nx2navi.rc: {r.stderr.strip()[:400]}')
    rsp = cache / 'link.rsp'
    rsp.write_text('\n'.join('"%s"' % o.as_posix() for o in [o for _, o in srcs] + [rco]), encoding='utf-8')
    exe = out / 'ocarina.exe'
    say('  linking')
    p = subprocess.run([str(clangxx), '-o', str(exe), '@' + str(rsp), '-L', str(msys / 'lib'), *LINK_LIBS],
                       capture_output=True, text=True, env=env)
    if p.returncode:
        print(p.stderr[-4000:], file=sys.stderr)
        fail('the link failed')
    shutil.copy2(gen / 'romfs-index.bin', out / 'romfs-index.bin')
    for name in ('nx2navi_xlate.exe', 'nx2navi_xlate.id'):
        shutil.copy2(KIT / 'bin' / name, out / name)
    if (KIT / 'bin' / 'amd_fidelityfx_vk.dll').is_file():   # FSR 3.1 (runtime/vk_ffx.cpp)
        shutil.copy2(KIT / 'bin' / 'amd_fidelityfx_vk.dll', out / 'amd_fidelityfx_vk.dll')
    say(f'  ocarina.exe {exe.stat().st_size / 1e6:.0f} MB; beside it: {", ".join(copy_dlls(exe, msys / "bin", out))}')
    (out / 'profile').mkdir(exist_ok=True)


# ---------------------------------------------------------------- main

def step_shaders(out):
    """Every shader of the game files into the game's shader cache and the driver's own cache, before the first start."""
    cmd = [sys.executable, str(KIT / 'tools' / 'prepare_shaders.py'), str(out), '--romfs', str(out / 'romfs'),
           '--exefs', str(out / 'exefs'), '--no-list', '--exe', 'ocarina.exe']
    if subprocess.run(cmd).returncode:
        fail('preparing the shaders failed (the lines above say why); the game still runs, compiling shaders as it '
             'meets them. Run the build again to retry this step')
    for seed in ('seed-programs.bin', 'seed-answers.bin'):   # translated now: the shader cache holds them
        (out / seed).unlink(missing_ok=True)


def keep_awake():
    """Hold off sleep for this process's life (Windows): the build runs for many minutes with no input, and a PC that
    sleeps in the middle stops the compiles. The hold ends with the process."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except (OSError, AttributeError):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--nup', help='the game as one .nup file')
    ap.add_argument('--exefs', help='the game\'s exefs folder (with --romfs, instead of --nup)')
    ap.add_argument('--romfs', help='the game\'s romfs folder')
    ap.add_argument('--out', help='the folder for the finished game')
    ap.add_argument('--check', action='store_true', help='only check the game\'s version; builds nothing')
    ap.add_argument('--msys', default=r'C:\msys64\ucrt64', help='MSYS2 UCRT64 folder (default C:\\msys64\\ucrt64)')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 4, help='parallel compiles')
    a = ap.parse_args()
    if bool(a.nup) == bool(a.exefs or a.romfs) or (not a.nup and not (a.exefs and a.romfs)):
        ap.error('give --nup, or both --exefs and --romfs')
    src = Source(a)
    check(src)
    say('check: Ocarina of Time 1.1.1 (build e0f3ccfa)')
    if a.check:
        return
    if not a.out:
        ap.error('--out is needed to build')
    msys = Path(a.msys)
    for need in ('bin/clang.exe', 'bin/clang++.exe', 'bin/SDL3.dll', 'lib/libSDL3.dll.a', 'include/vma'):
        if not (msys / need).exists():
            fail(f'{msys / need} is missing: install MSYS2 and the packages README-BuildKit.md lists')
    for mod in ('numpy', 'lz4', 'zbic'):
        try:
            __import__(mod)
        except ImportError:
            fail(f'the Python package {mod} is missing: pip install numpy lz4 zstandard zbic')
    out = Path(a.out).resolve()
    cache = out / 'build-cache'
    cache.mkdir(parents=True, exist_ok=True)
    state_path = cache / 'state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else {}
    if state.get('input') != src.identity or state.get('version') != STATE_VERSION or state.get('kit') != kit_version():
        state = {'input': src.identity, 'version': STATE_VERSION, 'kit': kit_version(), 'done': []}
    keep_awake()
    t0 = time.time()
    for name, run in (('exefs', lambda: step_exefs(src, out)), ('romfs', lambda: step_romfs(src, out, min(a.jobs, 8))),
                      ('lift', lambda: step_lift(out, cache)), ('compile', lambda: step_compile(out, cache, msys, a.jobs)),
                      ('shaders', lambda: step_shaders(out))):
        if name in state['done']:
            say(f'{name}: done before')
            continue
        say(f'{name}:')
        t = time.time()
        run()
        state['done'].append(name)
        state_path.write_text(json.dumps(state, indent=1), encoding='utf-8')
        say(f'{name}: {time.time() - t:.0f} s')
    say(f'\nfinished in {(time.time() - t0) / 60:.1f} min: {out / "ocarina.exe"}')


if __name__ == '__main__':
    main()
