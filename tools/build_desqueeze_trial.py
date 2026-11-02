"""Build the anamorphic-desqueeze EG (identical to the public preset).

The image contains only the shared Extensions menu framework (shutter sound,
default off) and the Extensions -> Desqueeze factor page (default OFF). It deliberately
omits aspect-profiles and the other presets.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def build(stock):
    """Same image as `patch.py anamorphic-desqueeze` (the public preset)."""
    from z30_extensions.build import build as build_preset
    return build_preset(stock, 'anamorphic-desqueeze')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Stock Z_30_0120.bin')
    p.add_argument('--output', type=Path, required=True, help='Output EG module path')
    p.add_argument('--deps', type=Path, help='Extra import path for Keystone')
    a = p.parse_args()
    if a.deps:
        sys.path.insert(0, str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    metadata = a.output.with_suffix('.json')
    if a.output.exists() or metadata.exists():
        p.error('Choose new output paths')
    image, report = build(extract_eg(a.source.read_bytes()))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('xb') as f:
        f.write(image)
    with metadata.open('x', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
        f.write('\n')
    print(f"{a.output}  sha256 {report['sha256']}")


if __name__ == '__main__':
    main()
