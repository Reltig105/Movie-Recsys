import pandas as pd
import os
import numpy as np
import nltk
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# Разрешаем NLTK скачивать словари через VPN/прокси
os.environ["NLTK_ALLOW_PROXIED_URLOPEN"] = "1"
nltk.download('stopwords')
from nltk.corpus import stopwords



def transform_df() -> tuple:
    df = pd.read_csv("ml/data/interim/tmdb_features.csv")
    df["overview"] = df["overview"].fillna("")

    #  обучение и векторизауия 
    custom_stops = ["фильм", "кино", "сюжет", "энди"]
    all_stop_words = stopwords.words("russian") + custom_stops
    
    vectorizer = TfidfVectorizer(
        stop_words=all_stop_words,
        # Учитываем не только слова, но и пары слов (биграммы)
        ngram_range=(1,2),
        # Слово должно встретиться минимум в 2 фильмах
        min_df=2, 
        max_df=0.7,
        # Заменяет TF на 1 + log(TF), сглаживая пики
        sublinear_tf=True 
    )

    tfidf_matrix = vectorizer.fit_transform(df["overview"])
    cosine_sim = cosine_similarity(tfidf_matrix)

    return df, cosine_sim



def get_recommendations(item_id: int, df: pd.DataFrame, cosine_sim, top_k: int = 5) -> list:
    if item_id not in df["item_id"].values:
        return []
    
    idx = df[df["item_id"]==item_id].index[0]
    sim_scores = list(enumerate(cosine_sim[idx]))

    sorted_scores = sorted(sim_scores, key = lambda x:x[1], reverse=True)
    top_scores = sorted_scores[1: top_k +1]
    top_idx = [i[0] for i in  top_scores]

    return df.iloc[top_idx][["item_id", "overview"]].to_dict(orient="records")



def save_artifacts(df: pd.DataFrame, cosine_sim) -> None:
    os.makedirs("ml/artifacts", exist_ok=True)

    np.save("ml/artifacts/cosine_sim.npy", cosine_sim)

    df[["item_id", "overview", "poster_path"]].to_pickle("ml/artifacts/movies_mapping.pkl")


if __name__ == "__main__":
    df, cosine_sim = transform_df()

    save_artifacts(df, cosine_sim)

    recommendations = get_recommendations(item_id=1, df = df, cosine_sim=cosine_sim, top_k=5)
    for i, rec in enumerate(recommendations,1):
        print(f"\n[{i}] Item ID: {rec['item_id']}")

