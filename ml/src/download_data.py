import io
import os
import urllib.request
import zipfile

DATA_URL = "https://files.grouplens.org/datasets/movielens/ml-100k.zip"
TARGET_DIR = os.path.join("ml", "data")
def download_and_extract_movielens() -> None:
    os.makedirs(TARGET_DIR, exist_ok=True)
    extract_path = os.path.join(TARGET_DIR, "ml-100k")
    
    if os.path.exists(extract_path):
        print(f"Датасет уже скачан и распакован: {extract_path}")
        return 
    
    print(f"Началась загрузка датасета по ссылке: {DATA_URL}")
    response = urllib.request.urlopen(DATA_URL)
    zip_buffer = io.BytesIO(response.read())

    print("Распаковка архива...")
    with zipfile.ZipFile(zip_buffer) as archive:
        archive.extractall(TARGET_DIR)

    print(f"Готово:{extract_path}")
if __name__ == "__main__":
    download_and_extract_movielens()



    