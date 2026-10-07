import os
import pandas as pd
import numpy as np
DATA_PATH = os.path.join("ml", "data", "ml-100k", "u.data")
COLUMNS = ["user_id", "item_id", "rating", "timestamp"]
ITEMS_PATH = os.path.join("ml", "data", "ml-100k", "u.item")

def load_ratings() -> pd.DataFrame:
    df = pd.read_csv(DATA_PATH, sep="\t", names=COLUMNS)
    return df

def calculate_sparsity(df: pd.DataFrame) -> float:
    n_users = df["user_id"].nunique()
    n_items = df["item_id"].nunique()
    n_ratings = len(df)
    sparsity = (1 - n_ratings / (n_items*n_users))*100
    return sparsity

def print_rating_distribution(df: pd.DataFrame) -> None:
    n_ratings = df["rating"].value_counts().sort_index()
    print("Распределение оценок:")
    print(n_ratings)

def print_user_activity(df: pd.DataFrame) -> None:
    c_ratings = df.groupby("user_id").size()
    mean = c_ratings.mean()
    median = c_ratings.median()
    print(f"Среднее число оценок на пользователя: {mean:.1f}")
    print(f"Медианное число оценок на пользователя: {median:.1f}")

def load_movies() -> pd.DataFrame:
    df = pd.read_csv(ITEMS_PATH, sep="|",encoding="ISO-8859-1", usecols=[0, 1], names=["item_id", "title"])
    return df

def print_top_movies(ratings_df: pd.DataFrame, movies_df: pd.DataFrame) -> None:
    merged_df = pd.merge(ratings_df, movies_df, on="item_id")
    top_movies = merged_df["title"].value_counts().head(5)
    print("--- Топ-5 самых оцениваемых фильмов ---")
    print(top_movies)

if __name__ == "__main__":
    df = load_ratings()
    sparsity = calculate_sparsity(df)
    movies_df = load_movies()
    print_top_movies(df, movies_df)

    print("--- Общая информация ---")
    print(f"Уникальных пользователей: {df['user_id'].nunique()}")
    print(f"Уникальных фильмов: {df['item_id'].nunique()}")
    print(f"Разреженность матрицы (Sparsity): {sparsity:.2f}%\n")
    
    print("--- Распределение оценок ---")
    print_rating_distribution(df)
    print()

    print("--- Активность пользователей ---")
    print_user_activity(df)



    




