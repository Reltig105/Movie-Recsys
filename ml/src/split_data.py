import pandas as pd
import numpy as np
from ml.src.config import PROCESSED_DATA_DIR, TRAIN_PATH, TEST_PATH

def temporal_train_test_split(df: pd.DataFrame, test_ratio: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values(by=["user_id", "timestamp"]).copy()
    df["rank"] = df.groupby("user_id").cumcount()
    df["total"] = df.groupby("user_id")["timestamp"].transform("count")

    train_mask = df["rank"] < df["total"] *(1-test_ratio)
    train_df = df[train_mask].drop(columns=["rank", "total"])
    test_df = df[~train_mask].drop(columns=["rank", "total"])

    return train_df, test_df

def save_splits(train_df: pd.DataFrame, test_df: pd.DataFrame) -> None:
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok = True)
    train_df.to_csv(TRAIN_PATH, index=False)
    test_df.to_csv(TEST_PATH, index=False)
    print("Сохранение прошло успешно")

if __name__ == "__main__":
    from ml.src.explore_data import load_ratings

    raw_df = load_ratings()
    train, test = temporal_train_test_split(raw_df, test_ratio=0.2)
    save_splits(train, test)
    print(f"Всего оценок: {len(raw_df)}")
    print(f"Оценок в Train: {len(train)} ({len(train) / len(raw_df) * 100:.1f}%)")
    print(f"Оценок в Test: {len(test)} ({len(test) / len(raw_df) * 100:.1f}%)")
    print(f"Колонки в train: {list(train.columns)}")
