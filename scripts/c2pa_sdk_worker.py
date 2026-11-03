"""Isolated SDK reader, invoked by c2pa_check to enforce a hard timeout."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='store_true')
    parser.add_argument('path', nargs='?')
    parser.add_argument('--settings')
    parser.add_argument('action', nargs='?', choices=['trust'])
    parser.add_argument('--trust_anchors')
    args = parser.parse_intermixed_args()
    if args.version:
        print('c2pa-python ' + version('c2pa-python'))
        return 0
    from c2pa import Context, Reader, C2paError
    settings = json.loads(Path(args.settings).read_text())
    if args.trust_anchors:
        settings['trust'] = {'trust_anchors': Path(args.trust_anchors).read_text()}
    try:
        with Context.from_dict(settings) as context:
            with Reader(args.path, context=context) as reader:
                print(reader.json())
        return 0
    except C2paError.ManifestNotFound:
        print('No claim found', file=sys.stderr)
        return 1
    except C2paError:
        # Raw errors can include paths or remote URLs from untrusted manifests.
        print('C2PA SDK could not validate this asset', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
