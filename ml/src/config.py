from pathlib import Path # модуль для работы для работы с путями file syst

ML_DIR = Path(__file__).resolve().parent.parent 
# __file__ путь к текущему файлу
# parent - поднимается на один уровень вверх к родительской папке

PROCESSED_DATA_DIR = ML_DIR / "data" / "processed"
TRAIN_PATH = PROCESSED_DATA_DIR / "train.csv"
TEST_PATH = PROCESSED_DATA_DIR / "test.csv"
