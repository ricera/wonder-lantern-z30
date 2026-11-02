"""Build the opt-in playback research EG: menu framework plus generic-fit observer.

The image contains only the shared Extensions menu framework (shutter sound,
default off) and the observation-only Playback probe page. It deliberately
omits aspect-profiles and the other presets so the log shows stock geometry.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def build(stock):
    from z30_extensions.plan import Plan
    from z30_extensions.features.shutter_sound import add
    plan = Plan(stock)
    add(plan, playback_probe=True)
    image, report = plan.finish()
    report.update(preset='playback-probe-research', diagnostic_only=True, research=True,
                  enabled_features=['shutter-sound-menu-framework', 'playback-probe'],
                  includes_full_features=False)
    return image, report


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
