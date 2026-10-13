import numpy as np
import pandas as pd

def calculate_rmse(y_true: np.ndarray | pd.Series, y_pred: np.ndarray | pd.Series) -> float:
    rmse = float(np.sqrt(np.mean((y_true - y_pred) **2)))
    return rmse

if __name__ == "__main__":
    true_ratings = np.array([5.0, 4.0, 3.0])
    pred_ratings = np.array([4.0, 4.0, 2.0])
    res = calculate_rmse(true_ratings, pred_ratings)
    print(f"{res:.4f}")


