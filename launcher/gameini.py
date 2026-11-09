"""The game's own settings, nx2navi.ini in the game folder, for the few the launcher's Settings switch.

Read the way runtime/cfg.c reads the file: a UTF-8 byte order mark is skipped, lines starting with #, ; or [ are
not settings, a key is matched without regard to case and with or without its NX2NAVI_ prefix, a value in double
quotes loses them, an empty value leaves the setting at its default, and the last line for a key wins. Sections do
not matter to the game; a key the file does not have yet goes under its section, as the game writes it.
A change rewrites only the line it touches, through a temporary file, so the rest of the file stays as it was.
"""
import os
from pathlib import Path

PREFIX = 'NX2NAVI_'


def _path(game_dir):
    return Path(game_dir) / 'nx2navi.ini'


def _key(text):
    k = text.strip().upper()
    return k[len(PREFIX):] if k.startswith(PREFIX) else k


def _lines(game_dir):
    try:   # newline='': the file's own line ends, which a change keeps
        with open(_path(game_dir), encoding='utf-8-sig', errors='replace', newline='') as f:
            text = f.read()
    except OSError:
        return []
    return text.splitlines(keepends=True)


def _setting(line):
    """(key, value) of a settings line, or None."""
    s = line.strip()
    if not s or s[0] in '#;[' or '=' not in s:
        return None
    k, v = s.split('=', 1)
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        v = v[1:-1]
    return _key(k), v


def get(game_dir, key):
    """The value the file gives key ('' when it gives none: the game's default applies)."""
    want, value = _key(key), ''
    for line in _lines(game_dir):
        kv = _setting(line)
        if kv and kv[0] == want:
            value = kv[1]
    return value


def put(game_dir, section, key, value):
    """Write key = value: on the key's last line when the file has one, else under [section] (made at the end when
    the file has none). The game folder must exist; the file is made when it does not."""
    lines, want = _lines(game_dir), _key(key)
    entry = f'{key.lower()} = {value}'
    nl = '\r\n' if lines and lines[0].endswith('\r\n') else '\n'
    at = max((i for i, line in enumerate(lines) if (_setting(line) or ('',))[0] == want), default=-1)
    if at >= 0:
        end = lines[at][len(lines[at].rstrip('\r\n')):] or nl
        lines[at] = entry + end
    else:
        head = next((i for i, line in enumerate(lines) if line.strip().lower() == f'[{section.lower()}]'), -1)
        if head >= 0:
            if not lines[head].endswith(('\n', '\r')):   # the header is the file's last line
                lines[head] += nl
            lines.insert(head + 1, entry + nl)
        else:
            if lines and not lines[-1].endswith(('\n', '\r')):
                lines[-1] += nl
            lines += [nl] if lines else []
            lines += [f'[{section}]{nl}', entry + nl]
    path = _path(game_dir)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(''.join(lines), encoding='utf-8', newline='')
    os.replace(tmp, path)
