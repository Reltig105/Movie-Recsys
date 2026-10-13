import pandas as pd
from ml.src.config import TRAIN_PATH, TEST_PATH
from ml.src.metrics import calculate_rmse

class ItemMeanBaseline:
    def __init__(self) -> None:
        self.global_mean: float = 0.0
        self.item_means: pd.Series = pd.Series(dtype=float)

    def fit(self, train_df: pd.DataFrame) -> None:
        self.global_mean = float(train_df["rating"].mean()) 
        self.item_means = train_df.groupby("item_id")["rating"].mean()

    def predict(self, test_df: pd.DataFrame) -> pd.Series:
        predictions = test_df["item_id"].map(self.item_means).fillna(self.global_mean)
        return predictions

if __name__ == "__main__":
    train_df = pd.read_csv(TRAIN_PATH)
    test_df = pd.read_csv(TEST_PATH)
    model = ItemMeanBaseline()
    model.fit(train_df)
    preds = model.predict(test_df)
    rmse = calculate_rmse(test_df["rating"], preds)
    print(f"Baseline RMSE: {rmse:.4f}")


    



