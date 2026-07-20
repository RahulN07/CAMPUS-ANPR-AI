import random
import shutil
from pathlib import Path


SOURCE_ROOT = Path(
    r"C:\Users\RAHUL NAYAK\Desktop\WEB DEVELOP\Web_10\dataset"
)

TARGET_ROOT = Path(
    r"C:\Users\RAHUL NAYAK\Desktop\campus-anpr-AI\dataset"
)

TRAIN_RATIO = 0.8
RANDOM_SEED = 42

SOURCE_FOLDERS = [
    SOURCE_ROOT / "vid-1",
    SOURCE_ROOT / "vid-2",
    SOURCE_ROOT / "vid-3",
]


def ensure_target_folders():
    folders = [
        TARGET_ROOT / "images" / "train",
        TARGET_ROOT / "images" / "val",
        TARGET_ROOT / "labels" / "train",
        TARGET_ROOT / "labels" / "val",
    ]

    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)


def collect_pairs():
    pairs = []

    for folder in SOURCE_FOLDERS:
        if not folder.exists():
            print(f"Warning: folder not found: {folder}")
            continue

        for image_path in folder.glob("*.jpg"):
            label_path = image_path.with_suffix(".txt")

            if not label_path.exists():
                print(f"Skipping image without label: {image_path.name}")
                continue

            pairs.append((folder.name, image_path, label_path))

    return pairs


def copy_pair(prefix, image_path, label_path, split_name):
    image_destination = (
        TARGET_ROOT
        / "images"
        / split_name
        / f"{prefix}_{image_path.name}"
    )

    label_destination = (
        TARGET_ROOT
        / "labels"
        / split_name
        / f"{prefix}_{label_path.name}"
    )

    shutil.copy2(image_path, image_destination)
    shutil.copy2(label_path, label_destination)


def write_data_yaml():
    yaml_path = TARGET_ROOT / "data.yaml"

    yaml_content = f"""path: {TARGET_ROOT.as_posix()}

train: images/train
val: images/val

names:
  0: license_plate
"""

    yaml_path.write_text(
        yaml_content,
        encoding="utf-8",
    )


def main():
    ensure_target_folders()

    pairs = collect_pairs()

    if not pairs:
        print("No image-label pairs were found.")
        return

    random.seed(RANDOM_SEED)
    random.shuffle(pairs)

    split_index = int(len(pairs) * TRAIN_RATIO)

    train_pairs = pairs[:split_index]
    val_pairs = pairs[split_index:]

    for prefix, image_path, label_path in train_pairs:
        copy_pair(
            prefix,
            image_path,
            label_path,
            "train",
        )

    for prefix, image_path, label_path in val_pairs:
        copy_pair(
            prefix,
            image_path,
            label_path,
            "val",
        )

    write_data_yaml()

    print("Dataset preparation completed.")
    print(f"Total pairs: {len(pairs)}")
    print(f"Training pairs: {len(train_pairs)}")
    print(f"Validation pairs: {len(val_pairs)}")
    print(f"Output folder: {TARGET_ROOT}")


if __name__ == "__main__":
    main()