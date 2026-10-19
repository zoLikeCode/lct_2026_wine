"""python -m portable.predict photo.jpg --model-dir /path/to/model_bundle"""
import argparse
import json
from pathlib import Path
from .images import MAX_BYTES, load_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--output', type=Path, help='Optional JSON output file')
    args = parser.parse_args()
    with args.image.open('rb') as stream:
        picture = load_image(stream.read(MAX_BYTES + 1))
    from .inference import Engine
    result = Engine(args.model_dir).predict(picture)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
