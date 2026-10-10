from pathlib import Path

ML_DIR = Path(__file__).resolve().parent.parent

PROCESSED_DATA_DIR = ML_DIR / "data" / "processed"
TRAIN_PATH = PROCESSED_DATA_DIR / "train.csv"
TEST_PATH = PROCESSED_DATA_DIR / "test.csv"
