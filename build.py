from pathlib import Path
import zipfile


def main():
    root = Path(__file__).resolve().parent
    destination = root / 'dist' / 'ytload.pyz'
    destination.parent.mkdir(exist_ok=True)
    sources = [root / 'ytload.py', root / 'LICENSE']
    sources += sorted(path for path in (root / 'ytloadlib').rglob('*')
                      if path.is_file() and path.suffix in {'.py', '.html', '.css', '.js', '.mjs', '.svg'}
                      and '__pycache__' not in path.parts)
    with destination.open('wb') as handle:
        handle.write(b'#!/usr/bin/env python3\n')
        with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sources:
                entry = zipfile.ZipInfo(source.relative_to(root).as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o644 << 16
                archive.writestr(entry, source.read_bytes())
            entry = zipfile.ZipInfo('__main__.py')
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, 'from ytload import main\nraise SystemExit(main())\n')
    destination.chmod(0o755)
    print(destination.relative_to(root))


if __name__ == '__main__':
    main()
